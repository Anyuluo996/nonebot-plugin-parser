"""抖音安卓 App detail 端点免签名直连形态。

``https://aweme.snssdk.com/aweme/v1/aweme/detail/`` 是安卓 App 使用的详情端点,
与 PC web 端点 (www.douyin.com/aweme/v1/web/aweme/detail/) 完全独立:
真实完整的设备参数下**不校验 a_bogus/X-Gorgon 等签名、不需要 Cookie**, 仅凭
iid/device_id 等设备标识即可取回完整 aweme_detail (响应结构与 web 端一致,
slides.decode_aweme_detail 可直接解码)。2026-10-10 经 Node 参照实现 jiexi
实测, 视频/图文/实况三类作品全通过。

设备参数从配置 ``parser_douyin_app_device_profile`` 注入 (JSON 字符串), 不写死
进仓库——iid/device_id 是可被服务端风控的个体标识, 硬编码在公开仓库等于把
设备烧掉。设备被风控 (表现为该端点也返回 200 + 空 body) 后重新提取即可:
真机/模拟器抓包 detail 请求的 query, 或走设备注册接口。

请求形态 (与 App 真机流量一致):
- query: 设备参数全量 + aweme_id + share 场景参数 + 每次刷新的 ts/_rticket;
- header: 移动端 UA + ``X-SS-No-Cookie: 1`` (明示游客态) + ``x-ss-dp`` (aid)
  + ``x-tt-store-region(-src)``; 不带 Referer/Origin/Cookie。

注意: 该形态请求失败时 httpx 异常 (如 HTTPStatusError) 的日志 repr 会带完整
URL, 其中含 iid/device_id——泄漏面仅部署者本地日志, 与既有 websign 形态日志
含 uifid 一致, 日志勿外传。
"""

import json
import time

from nonebot import logger

DETAIL_URL = "https://aweme.snssdk.com/aweme/v1/aweme/detail/"

# 安卓 App 上报的设备/应用参数白名单 (对齐 jiexi 参照实现)。iid/device_id/
# aid/version_code 必填, 其余按原值透传, 白名单外字段忽略 (防配置粘错)。
PROFILE_KEYS = frozenset(
    {
        "iid",
        "device_id",
        "ac",
        "channel",
        "aid",
        "app_name",
        "version_code",
        "version_name",
        "device_platform",
        "os",
        "ssmix",
        "device_type",
        "device_brand",
        "language",
        "os_api",
        "os_version",
        "manifest_version_code",
        "resolution",
        "dpi",
        "update_version_code",
        "package",
        "mcc_mnc",
        "first_launch_timestamp",
        "last_deeplink_update_version_code",
        "cpu_support64",
        "host_abi",
        "is_guest_mode",
        "app_type",
        "minor_status",
        "appTheme",
        "is_preinstall",
        "need_personal_recommend",
        "is_android_pad",
        "is_android_fold",
        "is_pad_landscape",
        "cdid",
    }
)
_REQUIRED_KEYS = ("iid", "device_id", "aid", "version_code")

_APP_UA = (
    "Mozilla/5.0 (Linux; Android 15; AOSP on Intel Platform) AppleWebKit/537.36 Chrome/121.0.0.0 Mobile Safari/537.36"
)

# 配置非法时每个原因只警告一次, 避免每次解析刷屏 (配置只能重启修改)
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message)


def parse_profile(raw: str | None) -> dict[str, str] | None:
    """解析配置里的设备参数 JSON; 未配置/非法返回 None (调用方跳过该形态)。

    - 值仅接受非空字符串与有限数字 (bool 除外), 统一转字符串;
    - 白名单外字段忽略; 必填 iid/device_id/aid/version_code 缺一即整体无效。
    """
    if not raw or not (stripped := raw.strip()):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        _warn_once("not-json", "parser_douyin_app_device_profile 不是合法 JSON, app-api 形态停用")
        return None
    if not isinstance(data, dict):
        _warn_once("not-object", "parser_douyin_app_device_profile 必须是 JSON 对象, app-api 形态停用")
        return None
    profile: dict[str, str] = {}
    for key, value in data.items():
        if key not in PROFILE_KEYS:
            _warn_once(f"unknown:{key}", f"parser_douyin_app_device_profile 忽略白名单外字段 {key!r}")
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            _warn_once(f"bad-value:{key}", f"parser_douyin_app_device_profile 字段 {key!r} 类型非法, 已忽略")
            continue
        text = str(value).strip()
        if text:
            profile[key] = text
    if missing := [key for key in _REQUIRED_KEYS if not profile.get(key)]:
        _warn_once(
            "missing",
            f"parser_douyin_app_device_profile 缺少必填字段 {missing}, app-api 形态停用",
        )
        return None
    return profile


def build_request(profile: dict[str, str], aweme_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """构造 app-api 形态的 ``(headers, params)``; ts/_rticket 每次调用刷新。

    场景参数 (origin_type=share 等) 取自 App 分享场景的真实流量形态;
    空值参数 (aweme_type/sub_scene/group_id/meta_params) App 端也原样上报,
    保留以贴近真机字节序。
    """
    now_ms = int(time.time() * 1000)
    params = {
        **profile,
        "aweme_id": aweme_id,
        "origin_type": "share",
        "request_source": "0",
        "is_story": "0",
        "location_permission": "0",
        "aweme_type": "",
        "recommend_collect_feedback": "0",
        "from_push": "0",
        "item_level": "0",
        "sub_scene": "",
        "group_id": "",
        "meta_params": "",
        "_rticket": str(now_ms),
        "ts": str(now_ms // 1000),
    }
    headers = {
        "User-Agent": _APP_UA,
        "Accept": "application/json",
        "X-SS-No-Cookie": "1",
        "x-ss-dp": profile["aid"],
        "x-tt-store-region": "cn",
        "x-tt-store-region-src": "did",
    }
    return headers, params
