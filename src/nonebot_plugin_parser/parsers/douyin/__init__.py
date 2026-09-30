import re
import time
import secrets
from typing import ClassVar

from nonebot import logger

from . import websign
from ..base import (
    Platform,
    BaseParser,
    PlatformEnum,
    ParseException,
    handle,
)
from ._abogus import ABogus
from ...exception import TipException

# PC web 详情接口
_DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

# 签名形态用的新版 UA，避免 COMMON_HEADER 里 2016 年的 Chrome/55 UBrowser
# 直接被抖音风控识别为异常客户端。仅作用于签名请求，不改全局 COMMON_HEADER
# （后者被各 parser / 下载器复用，贸然升级可能影响其它平台）。
_PC_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 抖音自家 SEO 爬虫 UA。detail 接口对 Bytespider 免 a_bogus 签名与登录态校验
# (2026-08-23 实测: 同机同 IP 仅换 UA, 免签名请求即返回完整 aweme_detail JSON,
# 浏览器 UA 则 200 + 空 body)。最后保险: 字节若给爬虫加反查校验此路即失效。
_BYTE_SPIDER_UA = "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com; +https://zhanzhang.toutiao.com/)"

# ABogus 签名器实例。内部状态在 get_value 调用时会 reset，实例可安全复用，
# 避免每次请求都重建对象（SM3 表/浏览器信息等初始化开销）。
_ABOGUS = ABogus()

# 签名形态的通用请求参数（仿抖音 web 端 getCommonData）。
# 这些字段会被 a_bogus 签名纳入计算，缺失会导致签名失效被风控返回空 body。
# 取值用常量而非动态读取 navigator.*，服务端仅做存在性 + 格式校验。
_PC_WEB_COMMON_PARAMS: dict[str, str] = {
    "aid": "6383",
    "channel": "channel_pc_web",
    "device_platform": "webapp",
    "pc_client_type": "1",
    "pc_libra_divert": "Windows",
    "version_code": "170400",
    "version_name": "17.4.0",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Edge",
    "browser_version": "132.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "132.0.0.0",
    "os_name": "Windows",
    "os_version": "10",
    "cpu_core_num": "16",
    "device_memory": "8",
    "platform": "PC",
    "effective_type": "4g",
    "round_trip_time": "100",
    "screen_width": "2195",
    "screen_height": "1235",
}


async def _request_story_service(service: str, params: dict[str, str]) -> dict | None:
    """请求故事采集服务 (MuMu 模拟器 + frida 常驻管线), 任何失败返回 None。

    服务侧单次采集含模拟器驱动最长 ~90s, 客户端超时 95s; 部署时需保证
    ``parser_parse_timeout`` 大于该值, 否则解析层先超时。
    服务走 easytier 内网, trust_env=False 绕开容器全局 http_proxy 环境变量
    (与 base.request / downloader 的项目约定一致)。
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(95.0), trust_env=False) as client:
            resp = await client.get(f"{service}/story", params=params)
        data = resp.json()
    except Exception as e:
        logger.warning(f"douyin story service request failed ({service}): {e!r}")
        return None
    return data if isinstance(data, dict) else None


class DouyinParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.DOUYIN, display_name="抖音")

    def __init__(self):
        super().__init__()
        # 抖音 douyinvod 视频直链 (detail API 的 play_addr) 需 Referer 防盗链校验,
        # 缺失则 403; COMMON_HEADER 全局通用仅含 UA, 在此给本 parser 下载头补上。
        self.headers["Referer"] = "https://www.douyin.com/"

    # https://v.douyin.com/_2ljF4AmKL8
    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("jx.douyin", r"jx\.douyin\.com/[a-zA-Z0-9_\-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    # https://www.douyin.com/video/7521023890996514083
    # https://www.douyin.com/note/7469411074119322899
    @handle("douyin", r"douyin\.com/(?P<ty>video|note|slides)/(?P<vid>\d+)")
    @handle("iesdouyin", r"iesdouyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    @handle("m.douyin", r"m\.douyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    # https://jingxuan.douyin.com/m/video/7574300896016862490?app=yumme&utm_source=copy_link
    @handle("jingxuan.douyin", r"jingxuan\.douyin.com/m/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    async def _parse_douyin(self, searched: re.Match[str]):
        # 三种类型(video/note/slides)统一走 PC detail API:
        #   - slides/note: 只有 detail API 返回 images[].video(实况照片视频),
        #     分享页数据没有该字段;
        #   - video: 自抖音 2026-08 改版, 分享页 _ROUTER_DATA 不再含 videoInfoRes,
        #     旧 parse_video 兜底已死(2026-09-07 统计 7 天 0 成功), 直链只由
        #     detail API 提供。
        # 链接类型仅用于 URL 匹配, 解析行为不再区分。
        return await self.parse_slides(searched.group("vid"))

    async def _request_detail(self, video_id: str):
        """依次以四种形态请求 PC detail 接口: open-api -> 签名 -> websign -> Bytespider。

        - open-api (主力): 极简参数 {aweme_id, aid} + Origin/Referer 指向
          open.douyin.com, 该入口不校验 a_bogus 签名与登录态, 2026-09 实测最稳
          (上游 fllesser #584 同款形态);
        - 签名: 完整 PC web 参数 + a_bogus 签名, 配置了 ttwid/cookie 时附凭据,
          对无签名形态整体被封的场景兜底;
        - websign (配置 uifid 才启用): 签名形态之上追加 secsdk 网页签名
          x-secsdk-web-signature + uifid (query 与三同名 header), 与官方页面
          请求形态一致;
        - Bytespider (最后保险): 爬虫 UA 免签名, 字节加反查校验即失效。

        Returns:
            最后一次成功发出的响应(可能为空 body), 每种形态都请求异常时为 None。
            空 body / None 由调用方统一转 ParseException。
        """
        import httpx

        from . import ttwid as dy_ttwid

        # 第三元素为 dict 时作 params 交给 httpx 编码, 为 str 时是已编码完整
        # URL (websign 形态, 预映像须与发送字节一致, 编码由 websign 全权负责)
        variants: list[tuple[str, dict[str, str], dict[str, str] | str]] = []

        # 1. open-api 形态 (主力)
        variants.append(
            (
                "open-api",
                {
                    **self.headers,
                    "Origin": "https://open.douyin.com",
                    "Referer": "https://open.douyin.com/",
                },
                {"aweme_id": video_id, "aid": "6383"},
            )
        )

        # 2. 签名形态: PC web UA + X-Requested-With 缺一不可, 否则风控返回
        # 200 + 空 body; 组装完整参数后追加 msToken(仿浏览器随机占位) 与
        # a_bogus 签名(必须最后追加)。配置了 ttwid/cookie 时附上, 抗风控能力
        # 显著更强 (凭据获取见 ttwid.get_effective_credential)。
        signed_headers = {
            **self.headers,
            "User-Agent": _PC_WEB_UA,
            "Referer": "https://www.douyin.com/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if credential := dy_ttwid.get_effective_credential():
            signed_headers["Cookie"] = credential
        signed_params: dict[str, str] = {
            **_PC_WEB_COMMON_PARAMS,
            "aweme_id": video_id,
            "msToken": secrets.token_hex(64),
        }
        # a_bogus 存**未编码**原文, 由 httpx 统一编码一次 (浏览器线上即单一
        # 编码)。旧实现先 quote 再入 dict, httpx 会把 % 再编成 %25 (双重编码),
        # 服务端解码一次得不到签名原文——签名形态间歇性空 body 的诱因之一。
        signed_params["a_bogus"] = _ABOGUS.get_value(signed_params)
        variants.append(("signed", signed_headers, signed_params))

        # 2b. websign 增强形态 (配置了 uifid 才启用; uifid 三级来源见
        # ttwid.get_effective_uifid)。签名覆盖 query 全部字节 (含 a_bogus)，
        # 故 query 由 websign 按secsdk 的 URLSearchParams 规则编码生成后
        # 拼成完整 URL 原样发送, 不再走 httpx params。
        if uifid := dy_ttwid.get_effective_uifid():
            query, _signature, sig_headers = websign.sign(signed_params, uifid)
            variants.append(("websign", {**signed_headers, **sig_headers}, f"{_DETAIL_URL}?{query}"))

        # 3. Bytespider 爬虫 UA (最后保险)
        variants.append(
            (
                "bytespider",
                {"User-Agent": _BYTE_SPIDER_UA},
                {
                    "aweme_id": video_id,
                    "aid": "6383",
                    "version_code": "170400",
                    "device_platform": "webapp",
                },
            )
        )

        response = None
        for name, form_headers, target in variants:
            try:
                if isinstance(target, str):
                    response = await self.request(target, headers=form_headers)
                else:
                    response = await self.request(_DETAIL_URL, headers=form_headers, params=target)
            except httpx.HTTPError as e:
                # 403 风控/限流/超时都归入此分支, 换下一形态重试
                logger.warning(f"douyin detail API ({name}) failed for {video_id}: {e!r}")
                continue
            if response.content:
                logger.debug(f"douyin detail API ({name}) succeeded for {video_id}")
                break
        return response

    async def _parse_story_via_service(self, video_id: str):
        """「故事」内容经模拟器采集服务解析, 服务未配置/失败返回 None。

        服务契约: GET {service}/story?id={aweme_id}[&token=...] →
        ``{"ok": true, "images": [签名直链...], "avatars": [url],
        "desc": str, "nickname": str}``; 图片为 14 天有效的 douyinpic
        签名直链 (App 渠道采集, 见记忆 douyin-mumu-frida-pipeline)。
        """
        from ...config import pconfig

        service = pconfig.douyin_story_service
        if not service:
            return None
        params: dict[str, str] = {"id": video_id}
        if token := pconfig.douyin_story_token:
            params["token"] = token
        data = await _request_story_service(service, params)
        if not data or not data.get("ok"):
            logger.warning(f"douyin story service no result for {video_id}: {str(data)[:200]}")
            return None
        raw_images = data.get("images") or []
        images = [u for u in raw_images if isinstance(u, str) and u.startswith("http")]
        if not images:
            return None
        avatars = data.get("avatars") or []
        avatar = avatars[0] if isinstance(avatars, list) and isinstance(avatars[0], str) else None
        nickname = data.get("nickname")
        author = self.create_author(
            nickname if isinstance(nickname, str) and nickname else "抖音用户",
            avatar,
        )
        desc = data.get("desc")
        return self.result(
            title=desc if isinstance(desc, str) and desc else "抖音「故事」",
            author=author,
            contents=self.create_image_contents(images),
            # 采集时刻近似发布时间 (服务侧无 create_time, 卡片仅显示日期)
            timestamp=int(time.time()),
        )

    async def parse_slides(self, video_id: str):
        from . import slides
        from ...utils import ffmpeg_available
        from ...config import pconfig

        response = await self._request_detail(video_id)
        if response is None or not response.content:
            raise ParseException(
                f"douyin detail API unavailable for {video_id} "
                "after open-api/signed/websign/bytespider attempts (likely risk-controlled)"
            )

        try:
            aweme_detail = slides.decode_aweme_detail(response.content)
        except Exception as e:
            # decode 失败可能是字段结构变更或返回了非 JSON 错误页
            preview = response.content[:200]
            logger.warning(
                f"decode douyin detail failed for {video_id}: {e!r} len={len(response.content)} preview={preview!r}"
            )
            raise ParseException(f"decode douyin detail failed for {video_id}: {e}") from e
        if aweme_detail is None:
            # 内容级过滤 (如 story_25_filter): 服务端明确不下发数据, 属确定性
            # 失败, 重试/换形态/L2 兜底都无意义 → TipException 立即提示不重试
            # (parse_retry 对 TipException 不重试, matchers 发提示消息)
            if reason := slides.extract_filter_reason(response.content):
                if reason.startswith("story"):
                    # 故事内容优先走模拟器采集服务 (配置了才有), 失败回退提示
                    if (story_result := await self._parse_story_via_service(video_id)) is not None:
                        return story_result
                    raise TipException("该内容是抖音「故事」，仅在抖音 App 内可见，无法解析")
                raise TipException(f"抖音未向网页端开放该内容（{reason}），无法解析")
            raise ParseException(f"can't find aweme_detail in PC detail API: {video_id}")

        contents = []

        # bgm_url 是随机 choice 的, 只取一次: 幻灯片与实况分支共用同一条 URL,
        # 同一 BGM 走同一缓存文件, 不重复下载
        bgm_url = aweme_detail.bgm_url

        if (
            (media_items := aweme_detail.media_items)
            and bgm_url
            and pconfig.douyin_note_slideshow
            and ffmpeg_available()
        ):
            # 图文(含实况照片混排)在 App 内是随 BGM 轮播的**单条**视频: 静态图
            # 按目标时长快切、实况照片按自身时长原速播放, 循环到 BGM 结束, 统一
            # 合成一条发送; 否则幻灯片 + 每条实况各发一视频 (线上 4ueJKZQ0tpI
            # 实测发了两条)。纯实况帖同样合成为一条。cache_key 带 v4: 合并语义
            # 变化后换 key, 已缓存旧产物自然过期。开关关闭或 ffmpeg 不可用时
            # 回退逐项发送(旧行为)。纯视频帖 media_items 为空, 不进此分支。
            cover_url = None
            if not any(kind == "image" for kind, _ in media_items):
                covers = aweme_detail.dynamic_cover_urls
                cover_url = covers[0] if covers else None
            contents.append(
                self.create_slideshow_content(
                    media_items, bgm_url, cache_key=f"douyin-slideshow-v4-{video_id}", cover_url=cover_url
                )
            )
        else:
            # 添加图片内容 (纯静态图)
            if image_urls := aweme_detail.image_urls:
                contents.extend(self.create_image_contents(image_urls))

            # 添加动态内容 (实况照片对应的 mp4 视频)
            if dynamic_urls := aweme_detail.dynamic_urls:
                contents.extend(
                    self.create_dynamic_contents(
                        dynamic_urls,
                        cover_urls=aweme_detail.dynamic_cover_urls,
                        bgm_url=bgm_url,
                    )
                )

        # 普通视频 (images/dynamic 均空, 顶层 video 含 play_addr)
        # 自抖音 2026-08 改版, 普通视频改由 detail API 提供直链。
        # `if not contents` 确保仅纯视频进此分支, 图文/实况不受影响。
        if not contents and (video_url := aweme_detail.video_url):
            contents.append(self.create_video_content(video_url, aweme_detail.cover_url, aweme_detail.duration_ms))

        # 构建作者
        author = self.create_author(aweme_detail.name, aweme_detail.avatar_url)

        return self.result(
            title=aweme_detail.desc,
            author=author,
            contents=contents,
            # SlidesData 是秒, PictureSlidesData 是毫秒, 用统一 property 兜齐
            timestamp=aweme_detail.create_time_seconds,
        )
