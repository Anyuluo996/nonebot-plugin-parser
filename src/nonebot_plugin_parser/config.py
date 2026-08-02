from pathlib import Path

from nonebot import logger, require, get_driver, get_plugin_config
from apilmoji import ELK_SH_CDN, EmojiStyle
from pydantic import Field, BaseModel, field_validator
from bilibili_api.video import VideoCodecs, VideoQuality

from .constants import RenderType, PlatformEnum

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as _store

_cache_dir: Path = _store.get_plugin_cache_dir()
_config_dir: Path = _store.get_plugin_config_dir()
_data_dir: Path = _store.get_plugin_data_dir()

# 获取全局配置和昵称（需要在 pconfig 之前初始化）
_driver = get_driver()
gconfig = _driver.config
"""全局配置"""
_nickname: str = next(iter(gconfig.nickname), "nonebot-plugin-parser")
"""机器人昵称"""


class Config(BaseModel):
    parser_bili_ck: str | None = None
    """bilibili cookies"""
    parser_ytb_ck: str | None = None
    """youtube cookies"""
    parser_xhs_ck: str | None = None
    """小红书 cookies"""
    parser_pixiv: str = "https://pixivnow-lyart.vercel.app"
    """PixivNow API 地址"""
    parser_pixivR18: bool = False
    """是否解析 R18 内容"""
    parser_ncm_api: str | None = None
    """[已弃用] 网易云音乐 API 地址，网易云现已直连官方接口，无需配置"""
    parser_proxy: str | None = None
    """代理"""
    parser_douyin_ttwid: str | None = None
    """抖音 PC web 详情接口用的登录态 ttwid Cookie（图文/实况照片解析）。

    抖音 PC detail 接口要求登录态凭据 + a_bogus 签名两者配套才放行: 仅 a_bogus
    或仅游客态 ttwid 都会返回 200 + 空 body。配置登录态 ttwid 后, 解析器会自动
    计算 a_bogus 签名, 即可恢复实况照片 (live photo) 视频解析。从浏览器登录抖音
    后复制 ``ttwid`` Cookie 填入即可; 留空则纯图文仍可正常解析 (走分享页兜底)。

    注: 这是兜底途径。推荐用 SUPERUSER 指令 ``dyttwid <值>`` 热更新（持久化到本地,
    优先级更高、无需重启，覆盖上一次的值），用 ``dyttwid查看`` 核对当前生效值。

    .. deprecated::
        仅带 ttwid 易被间歇性风控, 推荐改用 ``parser_douyin_cookie`` 配置完整登录态
        Cookie (含 ``sessionid``/``sid_guard``/``odin_tt`` 等)。本字段仍保留向后兼容,
        当 cookie 未配置时回退使用。
    """
    parser_douyin_cookie: str | None = None
    """抖音 PC web 详情接口用的完整登录态 Cookie（图文/实况照片解析）。

    比 ``parser_douyin_ttwid`` 更强的凭据: 整条浏览器 Cookie 字符串 (含
    ``sessionid``/``sid_guard``/``odin_tt``/``ttwid`` 等), 可大幅降低被风控返回空
    body 的概率。从浏览器 F12 → Network → www.douyin.com → Request Headers → Cookie
    整行复制填入。

    优先级高于 ``parser_douyin_ttwid``: 配置了 cookie 时忽略 ttwid。
    推荐 SUPERUSER 指令 ``dycookie <整条 Cookie>`` 热更新（持久化, 无需重启）,
    ``dycookie查看`` 核对当前生效值。
    """
    parser_douyin_cdn_via_proxy: bool = False
    """抖音 CDN 域名（douyinpic.com / snssdk.com 等）下载是否走代理。

    默认 False（直连，适合国内可直连抖音 CDN 的机器）。若部署机器直连抖音 CDN
    不通（如海外 / 受网络限制的服务器，表现为下载超时或 Connection reset），
    设为 True 让这些域名改走 ``parser_proxy`` 代理。
    """
    parser_need_upload: bool = False
    """是否需要上传音频文件"""
    parser_use_base64: bool = False
    """是否使用 base64 编码发送图片，音频，视频"""
    parser_max_size: int = 90
    """资源最大大小 默认 100 单位 MB"""
    parser_duration_maximum: int = 480
    """视频/音频最大时长"""
    parser_video_send_timeout: int = 30
    """视频下载首包等待阈值（秒）。超时后先发送封面图，视频后台继续下完再补发。

    避免因 CDN 节点慢（如 B 站 mcdn P2P 节点）导致用户长时间干等。
    实际下载重试仍按 download_file 的超时与 backup_urls 轮换进行，本阈值仅控制
    "封面先发" 的触发时机。设为 0 可禁用此行为（恢复等视频下完一起发的旧行为）。
    """
    parser_append_url: bool = False
    """是否在解析结果中附加原始URL"""
    parser_disabled_platforms: list[PlatformEnum] = Field(default_factory=list)
    """禁止的解析器"""
    parser_bili_video_codes: list[VideoCodecs] = Field(
        default_factory=lambda: [
            VideoCodecs.AVC,
            VideoCodecs.AV1,
            VideoCodecs.HEV,
        ]
    )
    """B站视频编码"""

    parser_bili_video_quality: VideoQuality = VideoQuality._1080P
    """B站视频分辨率"""
    parser_render_type: RenderType = RenderType.common
    """Renderer 类型"""
    parser_custom_font: str | None = None
    """自定义字体"""
    parser_need_forward_contents: bool = True
    """是否需要转发媒体内容"""
    parser_emoji_cdn: str = ELK_SH_CDN
    """Pilmoji 表情 CDN"""
    parser_emoji_style: EmojiStyle = EmojiStyle.FACEBOOK
    """Pilmoji 表情样式"""
    parser_force_prefix: str = ""
    """解析前缀，用于强制触发解析"""
    parser_screenshot: bool = True
    """短链重定向到无 handler 页面时，是否启用浏览器截图兜底（需安装 [htmlrender] extras）"""
    parser_screenshot_full_page: bool = False
    """浏览器截图是否整页（默认仅首屏）"""
    parser_tdl_path: str = "tdl"
    """tdl 二进制路径，默认走 PATH 查找，找不到可填绝对路径"""
    parser_tdl_ns: str = "default"
    """tdl session namespace，对应 `tdl login -n <ns>` 的命名空间"""
    parser_tdl_proxy: str | None = None
    """tdl 专用代理（tdl 不读 http_proxy 环境变量，必须显式 --proxy），为空时沿用 parser_proxy"""

    # ── 解析失败链接收集：重试(L2) + 上报(L3) ──────────────────────────
    parser_failure_retry_enabled: bool = True
    """是否启用失败链接定时重试（L2）"""
    parser_failure_retry_interval: int = 10
    """失败链接重试间隔（分钟）"""
    parser_failure_retry_max: int = 3
    """失败链接最大重试次数，超过后停止重试"""
    parser_failure_report_enabled: bool = False
    """是否启用失败链接上报到远程服务器（L3），默认关闭"""
    parser_failure_report_url: str | None = None
    """失败链接上报地址（HTTP，通常经 nginx 反代 + HTTPS）"""
    parser_failure_report_key: str | None = None
    """失败链接上报 API key（与服务端 API_KEY 一致）"""

    @field_validator("parser_bili_video_codes", mode="before")
    @classmethod
    def _coerce_bili_video_codes(cls, v: object) -> object:
        """兼容 bilibili-api 17.4.2 起 VideoCodecs 值由 str 变为 tuple 的变更。

        旧 .env / .env.test 里写的 ``["avc", "av01"]`` 在新版会被 pydantic 当作
        不合法的枚举值拒绝 (实际枚举值是 ``('avc',)``/``('av01','av1')``)。
        这里把短名 (大小写不敏感) 映射回枚举成员, 同时仍接受原生 tuple/成员输入。
        """
        if not isinstance(v, (list, tuple)):
            return v
        # 大小写不敏感短名 → 枚举成员映射表
        name_map = {c.name.lower(): c for c in VideoCodecs}
        # 把已知子串值也并入 (如 'avc'/'av01'/'hev'/'hvc'/'av1')
        for c in VideoCodecs:
            for part in c.value if isinstance(c.value, tuple) else (c.value,):
                if isinstance(part, str) and part.lower() not in name_map:
                    name_map[part.lower()] = c
        out: list[VideoCodecs] = []
        for item in v:
            if isinstance(item, VideoCodecs):
                out.append(item)
            elif isinstance(item, str):
                resolved = name_map.get(item.lower())
                if resolved is not None:
                    out.append(resolved)
                else:
                    # 解析不出, 原样返回交给 pydantic 报错
                    return v
            else:
                return v
        return out

    @property
    def nickname(self) -> str:
        """机器人昵称"""
        return _nickname

    @property
    def cache_dir(self) -> Path:
        """插件缓存目录"""
        return _cache_dir

    @property
    def config_dir(self) -> Path:
        """插件配置目录"""
        return _config_dir

    @property
    def data_dir(self) -> Path:
        """插件数据目录"""
        return _data_dir

    @property
    def max_size(self) -> int:
        """资源最大大小"""
        return self.parser_max_size

    @property
    def duration_maximum(self) -> int:
        """视频/音频最大时长"""
        return self.parser_duration_maximum

    @property
    def video_send_timeout(self) -> int:
        """视频下载首包等待阈值（秒），超时先发封面"""
        return self.parser_video_send_timeout

    @property
    def disabled_platforms(self) -> list[PlatformEnum]:
        """禁止的解析器"""
        return self.parser_disabled_platforms

    @property
    def bili_video_codes(self) -> list[VideoCodecs]:
        """B站视频编码"""
        return self.parser_bili_video_codes

    @property
    def bili_video_quality(self) -> VideoQuality:
        """B站视频分辨率"""
        return self.parser_bili_video_quality

    @property
    def render_type(self) -> RenderType:
        """Renderer 类型"""
        return self.parser_render_type

    @property
    def bili_ck(self) -> str | None:
        """bilibili cookies"""
        return self.parser_bili_ck

    @property
    def ytb_ck(self) -> str | None:
        """youtube cookies"""
        return self.parser_ytb_ck

    @property
    def xhs_ck(self) -> str | None:
        """小红书 cookies"""
        return self.parser_xhs_ck

    @property
    def pixiv(self) -> str:
        """PixivNow API 地址"""
        return self.parser_pixiv.rstrip("/")

    @property
    def pixivR18(self) -> bool:
        """是否解析 R18 内容"""
        return self.parser_pixivR18

    @property
    def ncm_api(self) -> str | None:
        """网易云音乐 API 地址（NeteaseCloudMusicApi），无尾斜杠"""
        if self.parser_ncm_api is None:
            return None
        return self.parser_ncm_api.rstrip("/")

    @property
    def proxy(self) -> str | None:
        """代理"""
        return self.parser_proxy

    @property
    def douyin_ttwid(self) -> str | None:
        """抖音 PC web 详情接口 ttwid Cookie，无首尾空白"""
        if self.parser_douyin_ttwid is None:
            return None
        return self.parser_douyin_ttwid.strip() or None

    @property
    def douyin_cookie(self) -> str | None:
        """抖音 PC web 详情接口完整 Cookie，无首尾空白/换行

        cookie 字符串含 ``;`` 分隔的多 key, 内部不应有换行 (浏览器复制的单行),
        strip() 去除首尾空白即可。
        """
        if self.parser_douyin_cookie is None:
            return None
        return self.parser_douyin_cookie.strip() or None

    @property
    def douyin_cdn_via_proxy(self) -> bool:
        """抖音 CDN 域名是否走代理下载"""
        return self.parser_douyin_cdn_via_proxy

    @property
    def need_upload(self) -> bool:
        """是否需要上传音频文件"""
        return self.parser_need_upload

    @property
    def use_base64(self) -> bool:
        """是否使用 base64 编码发送图片，音频，视频"""
        return self.parser_use_base64

    @property
    def append_url(self) -> bool:
        """是否在解析结果中附加原始URL"""
        return self.parser_append_url

    @property
    def custom_font(self) -> Path | None:
        """自定义字体"""
        if self.parser_custom_font:
            font_path = self.config_dir / self.parser_custom_font
            if font_path.exists():
                return font_path

            # 尝试从旧路径迁移字体文件
            old_path = self.data_dir / self.parser_custom_font
            if old_path.exists():
                try:
                    old_path.rename(font_path)
                    logger.info(f"字体文件 {old_path} 成功迁移到 {font_path}")
                except OSError:
                    logger.error(f"字体文件迁移失败, 请手动将其移动到 {font_path}")
                    return old_path

                return font_path

    @property
    def need_forward_contents(self) -> bool:
        """是否需要转发媒体内容"""
        return self.parser_need_forward_contents

    @property
    def emoji_cdn(self) -> str:
        """Pilmoji 表情 CDN"""
        return self.parser_emoji_cdn

    @property
    def emoji_style(self) -> EmojiStyle:
        """Pilmoji 表情样式"""
        return self.parser_emoji_style

    @property
    def parse_prefix(self) -> str:
        """解析前缀"""
        return self.parser_force_prefix.strip()

    @property
    def screenshot(self) -> bool:
        """是否启用截图兜底"""
        return self.parser_screenshot

    @property
    def screenshot_full_page(self) -> bool:
        """截图是否整页"""
        return self.parser_screenshot_full_page

    @property
    def tdl_path(self) -> str:
        """tdl 二进制路径"""
        return self.parser_tdl_path

    @property
    def tdl_ns(self) -> str:
        """tdl session namespace"""
        return self.parser_tdl_ns

    @property
    def tdl_proxy(self) -> str | None:
        """tdl 专用代理，为空时沿用 parser_proxy"""
        return self.parser_tdl_proxy or self.proxy

    @property
    def failure_retry_enabled(self) -> bool:
        """是否启用失败链接定时重试"""
        return self.parser_failure_retry_enabled

    @property
    def failure_retry_interval(self) -> int:
        """失败链接重试间隔（分钟）"""
        return self.parser_failure_retry_interval

    @property
    def failure_retry_max(self) -> int:
        """失败链接最大重试次数"""
        return self.parser_failure_retry_max

    @property
    def failure_report_enabled(self) -> bool:
        """是否启用失败链接上报"""
        return self.parser_failure_report_enabled

    @property
    def failure_report_url(self) -> str | None:
        """失败链接上报地址"""
        return self.parser_failure_report_url

    @property
    def failure_report_key(self) -> str | None:
        """失败链接上报 API key"""
        return self.parser_failure_report_key


pconfig: Config = get_plugin_config(Config)
"""插件配置"""
