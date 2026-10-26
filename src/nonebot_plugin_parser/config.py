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
    parser_x_ck: str | None = None
    """X(Twitter) cookies(auth_token=xxx; ct0=xxx; ..., 浏览器导出), 提升直连接口限流额度"""
    parser_pixiv: str = "https://pixivnow-lyart.vercel.app"
    """PixivNow API 地址"""
    parser_pixivR18: bool = False
    """是否解析 R18 内容"""
    parser_proxy: str | None = None
    """代理"""
    parser_verify_ssl: bool = True
    """是否校验 HTTPS 证书（TLS 校验），默认校验。

    登录 Cookie 与全部媒体都经该 TLS 链路传输，关闭校验等于放弃对中间人的防御，
    故默认 True。仅在部署环境证书链不完整（缺系统根证书 / 目标平台自签或漏装
    中间证书）导致请求报证书错误时，才显式设为 False 回退。
    """
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
    parser_douyin_uifid: str | None = None
    """抖音 PC web 详情接口的访客 ID UIFID（x-secsdk-web-signature 风控签名）。

    抖音对受保护接口（含 PC detail）校验 secsdk 网页签名, 签名与 ``uifid``
    （浏览器访问 www.douyin.com 后种下的 320 位十六进制访客身份 Cookie）配套。
    配置后, 签名形态请求会附带 uifid + x-secsdk-web-signature（query 与同名
    header 三件套）, 请求形态与官方页面一致, 进一步降低被风控返回空 body 的
    概率。从浏览器 F12 → Application → Cookies → www.douyin.com → ``UIFID``
    复制。

    也可不单独配置: 完整 Cookie（``parser_douyin_cookie``/``dycookie``）内已含
    UIFID 字段时自动提取。优先级: ``dyuifid`` 指令 > 本字段 > cookie 内提取。
    推荐 SUPERUSER 指令 ``dyuifid <值>`` 热更新（持久化, 无需重启）。
    """
    parser_douyin_cdn_via_proxy: bool = False
    """抖音 CDN 域名（douyinpic.com / snssdk.com 等）下载是否走代理。

    默认 False（直连，适合国内可直连抖音 CDN 的机器）。若部署机器直连抖音 CDN
    不通（如海外 / 受网络限制的服务器，表现为下载超时或 Connection reset），
    设为 True 让这些域名改走 ``parser_proxy`` 代理。
    """
    parser_douyin_note_slideshow: bool = True
    """抖音图文（静态图/实况照片 + BGM）是否合成幻灯片视频发送。

    抖音图文在 App 内以随 BGM 轮播的幻灯片视频形式播放。开启后，解析到图文带
    ``music.play_url`` 时，下载媒体与 BGM 合成**单条**轮播视频发送：静态图展示
    ``parser_douyin_slideshow_per_image`` 秒（图少循环快切、图多均摊），实况
    照片按自身时长原速播入同一条视频，循环到幻灯片结束；总长 =
    min(BGM 时长, ``parser_douyin_slideshow_max_duration``)。不再幻灯片 +
    实况各发一条。BGM 缺失时降级无声。关闭本开关或 ffmpeg 不可用时，回退为
    逐张发图 + 逐条实况视频（旧行为）。普通视频帖不受影响。
    """
    parser_douyin_slideshow_per_image: float = 5.0
    """图文幻灯片每张图的目标展示时长（秒）。

    仅在 ``parser_douyin_note_slideshow`` 开启时生效。图少 BGM 长时按该时长
    循环播放直到幻灯片结束；图多 BGM 短时自动均摊为总长/图数（保证每张图
    都出现）。设得越大视频越接近 App 原生效果（每图停留久），越小切换越快。
    <=0 视为 0.1 秒下限兜底。
    """
    parser_douyin_slideshow_max_duration: float = 60.0
    """图文幻灯片视频的总时长上限（秒）。

    幻灯片总长 = min(BGM 时长, 本上限)：BGM 短于上限时完整保留（音乐不截断），
    超长 BGM 只取开头一段（默认 60 秒），控制发送体积与合成耗时。
    <=0 视为不设上限（跟随完整 BGM）。
    """
    parser_douyin_slideshow_timeout: float = 90.0
    """图文幻灯片合成（ffmpeg）的超时（秒）。

    合成耗时随时长、分辨率与宿主负载波动大（60s 帖空闲实测 ~14s，4vCPU 负载
    尖峰下 30s 帖也曾贴穿 30s 的 video_send_timeout），故独立配置；到时 kill
    ffmpeg 子进程并降级直发原始媒体。<=0 回落 ``parser_video_send_timeout``。
    需完全不限时：把 ``parser_video_send_timeout`` 也置 <=0。
    """
    parser_douyin_story_service: str | None = None
    """抖音「故事」采集服务地址（MuMu 模拟器 + frida 常驻管线）。

    故事类内容被抖音服务端渠道过滤 (story_25_filter), web 端全链路拿不到
    数据。配置本项 (形如 ``http://10.x.x.x:18230``) 后, 解析器遇到故事内容
    时改调该服务在模拟器里驱动抖音 App 采集图片签名直链 (14 天有效)。
    服务不可用/采集失败时回退 TipException 提示, 不影响其它内容解析。

    注意: 采集含模拟器驱动, 最长 ~95 秒, 建议同时把 ``parser_parse_timeout``
    调到 110 以上, 否则解析层会先于采集完成而超时。留空 (默认) 不启用。
    """
    parser_douyin_story_token: str | None = None
    """故事采集服务的鉴权 token (与服务端 DY_STORY_TOKEN 环境变量一致)。"""
    parser_douyin_app_device_profile: str | dict[str, str | int | float | bool] | None = None
    """抖音安卓 App 详情端点 (aweme.snssdk.com) 的设备参数。

    该端点与 PC web 端点完全独立: 真实完整的设备参数下不校验 a_bogus 等签名、
    不需要 Cookie, 是 web 端四形态 (open-api/签名/websign/Bytespider) 之外的
    独立链路。配置后, open-api 形态失败 (风控空 body) 时优先切换此形态。

    值为 JSON 对象 (注意: NoneBot .env 管线会把形似 JSON 的值自动解码为
    dict, 故 str 与 dict 两种形态都接受), 形如::

        {"iid": "...", "device_id": "...", "aid": "1128", "version_code": "400600",
         "cdid": "...", "channel": "oppo_1128_64", "device_type": "...", ...}

    必填 iid/device_id/aid/version_code, 其余 App 上报参数按白名单透传, 未知
    字段忽略。参数从真机/模拟器抓包 detail 请求的 query 提取 (或设备注册接口)；
    设备被风控 (该端点也开始返回空 body) 时重新提取更新即可。

    注意: 不要把 Cookie/签名头填进本项, 该链路本就免凭据。留空 (默认) 不启用。
    """
    parser_hongguo_device_id: str | None = None
    """红果短剧 App 播放接口的 device_id（可选手动覆盖）。

    红果全集播放地址走番茄小说系 App 接口，要求一个注册过的 TT 设备标识。
    默认无需配置：首次解析时自动向字节设备注册接口注册设备并持久化到插件
    数据目录（``hongguo_device.json``），后续复用；接口开始返回空数据（设备
    被风控）时自动换新注册。仅当自动注册不可用（如网络层面被拦截）时，才
    需要从真机抓包手动填入 ``device_id`` 与 ``install_id`` 两者（缺一无效）。
    """
    parser_hongguo_install_id: str | None = None
    """红果短剧 App 播放接口的 install_id（与 ``parser_hongguo_device_id`` 配套）。"""
    parser_need_upload: bool = False
    """是否需要上传音频文件"""
    parser_use_base64: bool = False
    """是否使用 base64 编码发送图片，音频，视频"""
    parser_max_size: int = Field(default=90, ge=1)
    """资源最大大小 默认 90 单位 MB。

    必须 >=1: 下载层按 ``max_size * 1024 * 1024`` 算上限，0/负数会让所有媒体
    都被判为「超过上限」而无法下载。
    """
    parser_duration_maximum: int = Field(default=480, ge=1)
    """视频/音频最大时长（秒）。

    必须 >=1: 判据是 ``duration > duration_maximum``，0/负数会让所有视频都被
    判为超长而拒绝下载。需要「不限制」请填一个足够大的值。
    """
    parser_video_send_timeout: int = Field(default=30, ge=0)
    """媒体后处理等待阈值（秒），0 表示不限时。

    只约束下载完成之后的阶段: GIF 转换(palettegen+paletteuse)、渲染卡片取材
    (超时跳过该素材但不取消下载)。下载段不限时, 由下载层自身的重试预算兜底,
    两者不再互相截断(2026-09-25 推特 twimg 视频事故: 下载 35s 重试后成功,
    却先被渲染层 30s 超时判死)。
    """
    parser_parse_timeout: int = Field(default=90, ge=0)
    """解析顶层超时（秒）。解析超时后记录失败并打 warning 日志，不再无限挂起。

    仅作用于解析（元数据抓取）阶段，不影响后续渲染/视频下载（那两层各有自身超时）。
    Telegram 平台豁免（其解析阶段需同步下载媒体，tdl 自身 timeout=600s 兜底）。
    设为 0 可禁用此行为（恢复不限时等待的旧行为）。
    """
    parser_parse_retry_max: int = Field(default=3, ge=0)
    """解析失败即时重试次数（解析层，0 关闭）。单次解析最多尝试 1+N 次，全部失败才向用户报错。

    防抖音等平台结构改版/风控导致的偶发失败（快进快出的 403/空 body），几秒后
    重试往往即成功。超时、TipException/IgnoreException 不重试；Telegram 平台豁免
    （解析阶段含媒体下载，重试代价过高）。与失败链接后台定时重试
    （parser_failure_*，L2）是互补的两层机制：本层在用户消息上下文内即时重试、
    成功可直接发给用户；L2 无消息上下文，成功也无法回推。
    """
    parser_parse_retry_delay: float = Field(default=1.0, ge=0.0)
    """解析重试基础间隔（秒），按失败次数指数退避：1x → 2x → 4x；0 表示不等待"""
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
    parser_failure_retry_interval: int = Field(default=10, ge=1)
    """失败链接重试间隔（分钟）。必须 >=1：apscheduler interval 触发器的间隔不可为 0。"""
    parser_failure_retry_max: int = Field(default=3, ge=0)
    """失败链接最大重试次数，超过后停止重试（0 表示不重试、直接进入上报/标记）"""
    parser_failure_report_enabled: bool = False
    """是否启用失败链接上报到远程服务器（L3），默认关闭"""
    parser_failure_report_url: str | None = None
    """失败链接上报地址（HTTP，通常经 nginx 反代 + HTTPS）"""

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
        """媒体后处理等待阈值（秒），不约束下载段，0 表示不限时"""
        return self.parser_video_send_timeout

    @property
    def parse_timeout(self) -> int:
        """解析顶层超时（秒），超时记录失败不再挂起"""
        return self.parser_parse_timeout

    @property
    def parse_retry_max(self) -> int:
        """解析失败即时重试次数（0 关闭）"""
        return self.parser_parse_retry_max

    @property
    def parse_retry_delay(self) -> float:
        """解析重试基础间隔（秒）"""
        return self.parser_parse_retry_delay

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
    def x_ck(self) -> str | None:
        """X(Twitter) cookies"""
        return self.parser_x_ck

    @property
    def pixiv(self) -> str:
        """PixivNow API 地址"""
        return self.parser_pixiv.rstrip("/")

    @property
    def pixivR18(self) -> bool:
        """是否解析 R18 内容"""
        return self.parser_pixivR18

    @property
    def proxy(self) -> str | None:
        """代理"""
        return self.parser_proxy

    @property
    def verify_ssl(self) -> bool:
        """是否校验 HTTPS 证书"""
        return self.parser_verify_ssl

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
    def douyin_uifid(self) -> str | None:
        """抖音访客 ID UIFID（secsdk 网页签名绑定对象），无首尾空白"""
        if self.parser_douyin_uifid is None:
            return None
        return self.parser_douyin_uifid.strip() or None

    @property
    def douyin_cdn_via_proxy(self) -> bool:
        """抖音 CDN 域名是否走代理下载"""
        return self.parser_douyin_cdn_via_proxy

    @property
    def douyin_note_slideshow(self) -> bool:
        """抖音纯图文是否合成幻灯片视频发送"""
        return self.parser_douyin_note_slideshow

    @property
    def douyin_slideshow_per_image(self) -> float:
        """图文幻灯片每张图目标展示时长（秒），兜底 >= 0.1"""
        return max(self.parser_douyin_slideshow_per_image, 0.1)

    @property
    def douyin_slideshow_max_duration(self) -> float:
        """图文幻灯片视频总时长上限（秒）；<=0 视为不设上限"""
        value = self.parser_douyin_slideshow_max_duration
        return value if value > 0 else float("inf")

    @property
    def douyin_slideshow_timeout(self) -> float:
        """图文幻灯片合成超时（秒）；<=0 回落 video_send_timeout"""
        value = self.parser_douyin_slideshow_timeout
        return value if value > 0 else float(self.video_send_timeout)

    @property
    def douyin_story_service(self) -> str | None:
        """抖音故事采集服务地址，无首尾空白与尾斜杠"""
        if self.parser_douyin_story_service is None:
            return None
        return self.parser_douyin_story_service.strip().rstrip("/") or None

    @property
    def douyin_story_token(self) -> str | None:
        """抖音故事采集服务鉴权 token"""
        if self.parser_douyin_story_token is None:
            return None
        return self.parser_douyin_story_token.strip() or None

    @property
    def douyin_app_device_profile(self) -> str | dict | None:
        """抖音 App 端点设备参数，str 时无首尾空白，否则原样 (dict/None)"""
        if self.parser_douyin_app_device_profile is None:
            return None
        if isinstance(self.parser_douyin_app_device_profile, str):
            return self.parser_douyin_app_device_profile.strip() or None
        return self.parser_douyin_app_device_profile

    @property
    def hongguo_device_id(self) -> str | None:
        """红果 App 接口 device_id 手动覆盖值（纯数字才有效）"""
        if self.parser_hongguo_device_id is None:
            return None
        value = self.parser_hongguo_device_id.strip()
        return value if value.isdigit() else None

    @property
    def hongguo_install_id(self) -> str | None:
        """红果 App 接口 install_id 手动覆盖值（纯数字才有效）"""
        if self.parser_hongguo_install_id is None:
            return None
        value = self.parser_hongguo_install_id.strip()
        return value if value.isdigit() else None

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


pconfig: Config = get_plugin_config(Config)
"""插件配置"""

# 关闭 TLS 校验属于安全降级：登录 Cookie 与全部媒体都走这条链路，中间人可读可改。
# 放在模块层而非 __init__，是为了让任何导入 config 的路径（含仅加载解析器的场景）
# 都能看到这条告警；部署环境证书链正常时不应出现该日志。
if not pconfig.verify_ssl:
    logger.warning(
        "parser_verify_ssl=False：凭据与媒体将不经 TLS 证书校验，存在中间人劫持风险；"
        "仅在部署环境证书链不完整时使用，证书正常请改回 True。"
    )
