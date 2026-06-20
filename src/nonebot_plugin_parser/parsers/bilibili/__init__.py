import json
import asyncio
from re import Match
from typing import ClassVar
from collections.abc import AsyncGenerator

from msgspec import MsgspecError, convert
from nonebot import logger
from bilibili_api import HEADERS, Credential, select_client, request_settings
from bilibili_api.opus import Opus
from bilibili_api.video import Video
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents

from ..base import (
    BaseParser,
    PlatformEnum,
    ParseException,
    IgnoreException,
    DownloadException,
    handle,
    pconfig,
)
from ..data import Platform, ParseResult, ImageContent, MediaContent
from ..cookie import ck2dict
from .dynamic import DynamicInfo

# 转发链递归深度上限，防止循环引用/极深嵌套导致 RecursionError 崩溃
MAX_REPOST_DEPTH = 5


def _safe_convert(raw, target_type, *, context: str):
    """安全 msgspec 转换：API 返回结构变化时抛 ParseException 而非 ValidationError 崩溃。

    B 站 API 在风控/改版中字段经常变动，msgspec.convert 缺字段/类型不符会抛
    ValidationError，未捕获会让整条解析直接失败。
    """
    try:
        return convert(raw, target_type)
    except MsgspecError as e:
        logger.warning(f"B站接口数据结构异常（{context}）: {e}")
        raise ParseException(f"B站接口数据解析失败（{context}）") from e


# 选择客户端
select_client("curl_cffi")
# 模拟浏览器，第二参数数值参考 curl_cffi 文档
# https://curl-cffi.readthedocs.io/en/latest/impersonate.html
request_settings.set("impersonate", "chrome131")


class BilibiliParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.BILIBILI, display_name="哔哩哔哩")

    def __init__(self):
        super().__init__()
        self.headers = HEADERS.copy()
        self._credential: Credential | None = None
        self._cookies_file = pconfig.config_dir / "bilibili_cookies.json"

    @handle("b23.tv", r"b23\.tv/[A-Za-z\d\._?%&+\-=/#]+")
    @handle("bili2233", r"bili2233\.cn/[A-Za-z\d\._?%&+\-=/#]+")
    async def _parse_short_link(self, searched: Match[str]) -> ParseResult:
        """解析短链

        重定向后若匹配不到 handler（会员购商城/漫画等子站），走浏览器截图兜底。
        """
        url = f"https://{searched.group(0)}"
        logger.info(f"B站短链解析: {url}")
        redirect_url = await self.get_redirect_url(url)
        if redirect_url == url:
            raise ParseException(f"无法重定向 URL: {url}")
        logger.info(f"URL 重定向: {url} -> {redirect_url}")
        try:
            keyword, searched_new = self.search_url(redirect_url)
        except ParseException:
            logger.info(f"重定向 URL 无匹配 handler，走浏览器截图: {redirect_url}")
            return await self._screenshot_fallback(redirect_url)
        logger.info(f"重定向 URL 匹配到: {keyword}")
        result = await self.parse(keyword, searched_new)
        logger.info(f"短链重定向解析完成: {result.title}")
        return result

    async def _screenshot_fallback(self, target_url: str) -> ParseResult:
        """短链重定向到无 handler 页面（会员购/漫画等）时，浏览器截图兜底"""
        from ...browser import screenshot_url, is_browser_available
        from ...exception import TipException

        if not pconfig.screenshot:
            # 关闭截图兜底则保留原"无法匹配"行为
            raise ParseException(f"无法匹配 {target_url}")
        if not is_browser_available():
            raise TipException(
                "B站短链指向暂不支持解析的页面，且未安装截图依赖\n"
                '请安装: uv add "nonebot-plugin-parser[htmlrender]"'
                " 并执行 playwright install chromium\n"
                f"链接: {target_url}"
            )
        try:
            path, title = await screenshot_url(target_url, full_page=pconfig.screenshot_full_page)
        except Exception as e:
            logger.exception(f"页面截图失败: {target_url}")
            raise ParseException(f"页面截图失败: {e}")
        return self.result(
            url=target_url,
            title=title or "B站链接截图",
            contents=[ImageContent(path)],
            extra={"content_type": "网页截图"},
        )

    @handle("BV", r"^(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle("/BV", r"bilibili\.com(?:/video)?/(?P<bvid>BV[0-9a-zA-Z]{10})")
    async def _parse_bv(self, searched: Match[str]):
        """解析视频信息"""
        bvid = str(searched.group("bvid"))
        # 处理 page_num 可能不存在的情况
        try:
            page_num = int(searched.group("page_num") or 1)
        except (AttributeError, IndexError, ValueError):
            page_num = 1

        return await self.parse_video(bvid=bvid, page_num=page_num)

    @handle("av", r"^av(?P<avid>\d{6,})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle("/av", r"bilibili\.com(?:/video)?/av(?P<avid>\d{6,})")
    async def _parse_av(self, searched: Match[str]):
        """解析视频信息"""
        avid = int(searched.group("avid"))
        # 处理 page_num 可能不存在的情况
        try:
            page_num = int(searched.group("page_num") or 1)
        except (AttributeError, IndexError, ValueError):
            page_num = 1

        return await self.parse_video(avid=avid, page_num=page_num)

    @handle("/dynamic/", r"bilibili\.com/dynamic/(?P<dynamic_id>\d+)")
    @handle("t.bili", r"t\.bilibili\.com/(?P<dynamic_id>\d+)")
    @handle("/opus/", r"bilibili\.com/opus/(?P<dynamic_id>\d+)")
    async def _parse_dynamic(self, searched: Match[str]):
        """解析动态信息"""
        dynamic_id = int(searched.group("dynamic_id"))
        return await self.parse_dynamic_or_opus(dynamic_id)

    @handle("live.bili", r"live\.bilibili\.com/(?P<room_id>\d+)")
    async def _parse_live(self, searched: Match[str]):
        """解析直播信息"""
        room_id = int(searched.group("room_id"))
        return await self.parse_live(room_id)

    @handle("/favlist", r"favlist\?fid=(?P<fav_id>\d+)")
    async def _parse_favlist(self, searched: Match[str]):
        """解析收藏夹信息"""
        fav_id = int(searched.group("fav_id"))
        return await self.parse_favlist(fav_id)

    @handle("/read/", r"bilibili\.com/read/cv(?P<read_id>\d+)")
    async def _parse_read(self, searched: Match[str]):
        """解析专栏信息"""
        from bilibili_api.article import Article

        read_id = int(searched.group("read_id"))
        article = Article(read_id)
        opus = await article.turn_to_opus()
        return await self._parse_bilibli_api_opus(opus)

    async def parse_video(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_num: int = 1,
    ):
        """解析视频信息"""

        from .video import VideoInfo, AIConclusion

        video = await self._get_video(bvid=bvid, avid=avid)
        video_info = _safe_convert(await video.get_info(), VideoInfo, context="视频信息")
        # UP
        author = self.create_author(video_info.owner.name, video_info.owner.face)
        # 处理分 p
        page_info = video_info.extract_info_with_page(page_num)

        # 获取 AI 总结
        if self._credential:
            cid = await video.get_cid(page_info.index)
            ai_conclusion = await video.get_ai_conclusion(cid)
            ai_conclusion = _safe_convert(ai_conclusion, AIConclusion, context="AI总结")
            ai_summary = ai_conclusion.summary
        else:
            ai_summary: str = "哔哩哔哩 cookie 未配置或失效, 无法使用 AI 总结"

        url = f"https://bilibili.com/{video_info.bvid}"
        url += f"?p={page_info.index + 1}" if page_info.index > 0 else ""

        # 视频下载 task
        async def download_video():
            output_path = pconfig.cache_dir / f"{video_info.bvid}-{page_num}.mp4"
            if output_path.exists():
                return output_path
            v_url, a_url = await self.extract_download_urls(video=video, page_index=page_info.index)
            if page_info.duration > pconfig.duration_maximum:
                logger.warning(f"视频时长 {page_info.duration} 秒, 超过 {pconfig.duration_maximum} 秒, 取消下载")
                raise IgnoreException
            if a_url is not None:
                return await self.downloader.download_av_and_merge(
                    v_url, a_url, output_path=output_path, ext_headers=self.headers
                )
            else:
                return await self.downloader.download_file(v_url, file_name=output_path.name, ext_headers=self.headers)

        video_task = asyncio.create_task(download_video())
        video_content = self.create_video_content(
            video_task,
            page_info.cover,
            page_info.duration,
        )

        return self.result(
            url=url,
            title=page_info.title,
            timestamp=page_info.timestamp,
            text=video_info.desc,
            author=author,
            contents=[video_content],
            extra={"info": ai_summary},
        )

    async def parse_dynamic_or_opus(self, dynamic_id: int):
        """解析动态或图文"""
        from bilibili_api.dynamic import Dynamic

        from .dynamic import DynamicWrapper

        dynamic = Dynamic(dynamic_id, await self.credential)
        if await dynamic.is_article():
            return await self._parse_bilibli_api_opus(dynamic.turn_to_opus())

        dynamic_info = _safe_convert(await dynamic.get_info(), DynamicWrapper, context="动态信息").item
        return await self._parse_dynamic_info(dynamic_info)

    async def _parse_dynamic_info(self, dynamic_info: DynamicInfo, depth: int = 0):
        if dynamic_info.is_video():
            if (major := dynamic_info.modules.major) and (archive := major.archive):
                result = await self.parse_video(bvid=archive.bvid)
                result.text = dynamic_info.text
                result.extra["content_type"] = "动态"
                return result

        # 下载图片
        author = self.create_author(dynamic_info.name, dynamic_info.avatar)
        contents: list[MediaContent] = []
        contents.extend(self.create_image_contents(dynamic_info.image_urls))

        repost = None
        # 限制转发链递归深度，防止循环引用/极深嵌套导致 RecursionError 崩溃
        if dynamic_info.type == "DYNAMIC_TYPE_FORWARD" and dynamic_info.orig is not None and depth < MAX_REPOST_DEPTH:
            repost = await self._parse_dynamic_info(dynamic_info.orig, depth + 1)

        return self.result(
            title=dynamic_info.title,
            text=dynamic_info.text,
            timestamp=dynamic_info.timestamp,
            author=author,
            contents=contents,
            repost=repost,
            extra={"content_type": "动态"},
        )

    async def parse_opus_by_id(self, opus_id: int):
        """解析图文动态(opus id)"""
        opus = Opus(opus_id, await self.credential)
        return await self._parse_bilibli_api_opus(opus)

    async def _parse_bilibli_api_opus(self, bili_opus: Opus):
        """解析图文动态(Opus)"""

        from .opus import OpusItem

        opus_info = await bili_opus.get_info()
        if not isinstance(opus_info, dict):
            raise ParseException("获取图文动态信息失败")
        # 转换为结构体
        opus_data = _safe_convert(opus_info, OpusItem, context="图文动态")
        logger.debug(f"opus_data: {opus_data}")
        author = self.create_author(*opus_data.name_avatar)

        # 按顺序处理图文内容
        graphics = self.create_empty_graphics()
        for node in opus_data.extract_nodes():
            if isinstance(node, str):
                graphics.append(node)
            else:
                graphics.append(self.create_image_content(node.url, alt=node.alt))

        return self.result(
            title=opus_data.title,
            author=author,
            timestamp=opus_data.timestamp,
            graphics=graphics,
        )

    async def parse_live(self, room_id: int):
        """解析直播"""
        from bilibili_api.live import LiveRoom

        from .live import RoomData

        room = LiveRoom(room_display_id=room_id, credential=await self.credential)
        info_dict = await room.get_room_info()

        room_data = _safe_convert(info_dict, RoomData, context="直播信息")
        contents: list[MediaContent] = []
        # 下载封面
        if cover := room_data.cover:
            cover_task = self.downloader.download_img(cover, ext_headers=self.headers)
            contents.append(ImageContent(cover_task))

        # 下载关键帧
        if keyframe := room_data.keyframe:
            keyframe_task = self.downloader.download_img(keyframe, ext_headers=self.headers)
            contents.append(ImageContent(keyframe_task))

        author = self.create_author(room_data.name, room_data.avatar)

        url = f"https://www.bilibili.com/blackboard/live/live-activity-player.html?enterTheRoom=0&cid={room_id}"
        return self.result(
            url=url,
            title=room_data.title,
            text=room_data.detail,
            contents=contents,
            author=author,
        )

    async def parse_favlist(self, fav_id: int):
        """解析收藏夹"""
        from bilibili_api.favorite_list import get_video_favorite_list_content

        from .favlist import FavData

        # 只会取一页，20 个
        fav_dict = await get_video_favorite_list_content(fav_id)

        if fav_dict["medias"] is None:
            raise ParseException("收藏夹内容为空, 或被风控")

        favdata = _safe_convert(fav_dict, FavData, context="收藏夹")

        author = self.create_author(favdata.info.upper.name, favdata.info.upper.face)

        graphics: list[str | ImageContent] = []
        for fav in favdata.medias:
            graphics.append(self.create_image_content(fav.cover, alt=fav.desc))
            graphics.append(fav.desc)

        return self.result(
            title=favdata.title,
            timestamp=favdata.timestamp,
            author=author,
            graphics=graphics,
        )

    async def _get_video(self, *, bvid: str | None = None, avid: int | None = None) -> Video:
        """解析视频"""
        if avid:
            return Video(aid=avid, credential=await self.credential)
        elif bvid:
            return Video(bvid=bvid, credential=await self.credential)
        else:
            raise ParseException("avid 和 bvid 至少指定一项")

    async def extract_download_urls(
        self,
        video: Video | None = None,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_index: int = 0,
    ) -> tuple[str, str | None]:
        """解析视频下载链接"""

        from bilibili_api.video import (
            AudioStreamDownloadURL,
            VideoStreamDownloadURL,
            VideoDownloadURLDataDetecter,
        )

        if video is None:
            video = await self._get_video(bvid=bvid, avid=avid)

        # 获取下载数据
        download_url_data = await video.get_download_url(page_index=page_index)
        detecter = VideoDownloadURLDataDetecter(download_url_data)
        try:
            streams = detecter.detect_best_streams(
                video_max_quality=pconfig.bili_video_quality,
                codecs=pconfig.bili_video_codes,
                no_dolby_video=True,
                no_hdr=True,
            )
        except AttributeError:
            # bilibili_api detect_best_streams 排序时 codecs=None 的流会触发 AttributeError
            # 上游 issue #1035: hvc1/hev1 等编码无法匹配 VideoCodecs.value("hev")
            # → video_codecs 残留 None → 排序崩溃
            # 降级: 从原始 dash 数据重新解析(用自定义 codec 映射识别 hvc1)，手动过滤再选最佳
            logger.debug("detect_best_streams() failed (likely codecs=None), using fallback")
            streams = self._fallback_select_streams(
                download_url_data,
                max_quality=pconfig.bili_video_quality,
                allowed_codecs=pconfig.bili_video_codes,
            )

        video_stream = streams[0]
        if not isinstance(video_stream, VideoStreamDownloadURL):
            raise DownloadException("未找到可下载的视频流")
        logger.debug(f"视频流质量: {video_stream.video_quality.name}, 编码: {video_stream.video_codecs}")

        audio_stream = streams[1]
        if not isinstance(audio_stream, AudioStreamDownloadURL):
            return video_stream.url, None
        logger.debug(f"音频流质量: {audio_stream.audio_quality.name}")
        return video_stream.url, audio_stream.url

    # B站 dash 视频流 codecs 字符串 → VideoCodecs 映射
    # 上游 issue #1035: VideoCodecs.HEV.value="hev" 无法匹配 "hvc1.x.x"，导致 codecs=None
    # 这里用自定义前缀映射兜底识别 hvc1/hev1 等变体
    _CODEC_PREFIX_MAP: ClassVar[dict[str, str]] = {
        "hvc1": "HEV",
        "hev1": "HEV",
        "hvc": "HEV",
        "avc1": "AVC",
        "avc": "AVC",
        "av01": "AV1",
        "av1": "AV1",
    }

    @staticmethod
    def _resolve_codecs(codecs_str: str):
        """根据 dash 返回的 codecs 字符串识别 VideoCodecs，识别不了返回 None"""
        from bilibili_api.video import VideoCodecs

        if not codecs_str:
            return None
        lower = codecs_str.lower()
        for prefix, name in BilibiliParser._CODEC_PREFIX_MAP.items():
            if prefix in lower:
                return getattr(VideoCodecs, name, None)
        return None

    @staticmethod
    def _fallback_select_streams(
        download_url_data: dict,
        *,
        max_quality=120,
        allowed_codecs: list | None = None,
    ) -> list:
        """bilibili_api detect_best_streams 降级: 直接从 dash 原始数据重新解析选最佳流

        绕开上游 issue #1035 中 detect() 把 hvc1 流的 video_codecs 置为 None 的问题：
        VideoStreamDownloadURL 构造后并未保留原始 codecs 字符串，所以这里从 dash dict
        重新提取，用 _resolve_codecs 自行识别编码。

        上游修复后(VideoCodecs.HEV.value 变成 tuple) detect_best_streams 不再抛异常，
        本方法不会被调用，自动成为 no-op。
        """
        from bilibili_api.video import (
            AudioQuality,
            VideoQuality,
            AudioStreamDownloadURL,
            VideoStreamDownloadURL,
        )

        max_qv = max_quality.value if hasattr(max_quality, "value") else max_quality
        allowed = set(allowed_codecs) if allowed_codecs is not None else None

        video_streams: list[VideoStreamDownloadURL] = []
        audio_streams: list[AudioStreamDownloadURL] = []

        dash = download_url_data.get("dash") or {}
        # bangumi 数据可能多包一层 video_info
        if not dash and download_url_data.get("video_info"):
            dash = download_url_data["video_info"].get("dash") or {}

        for vd in dash.get("video", []) or []:
            try:
                q = VideoQuality(vd["id"])
            except (KeyError, ValueError):
                continue
            # 忽略 HDR/杜比/超 max 的清晰度
            if q in (VideoQuality.HDR, VideoQuality.DOLBY):
                continue
            if q.value > max_qv:
                continue
            url = vd.get("baseUrl") or vd.get("base_url")
            if not url:
                continue
            codecs_enum = BilibiliParser._resolve_codecs(vd.get("codecs", ""))
            if codecs_enum is None:
                # 识别不出编码的流直接丢弃，避免再次触发上游排序崩溃
                continue
            if allowed is not None and codecs_enum not in allowed:
                continue
            video_streams.append(VideoStreamDownloadURL(url=url, video_quality=q, video_codecs=codecs_enum))

        for ad in dash.get("audio", []) or []:
            try:
                q = AudioQuality(ad["id"])
            except (KeyError, ValueError):
                continue
            url = ad.get("baseUrl") or ad.get("base_url")
            if not url:
                continue
            if q.value > AudioQuality._192K.value:
                continue
            audio_streams.append(AudioStreamDownloadURL(url=url, audio_quality=q))

        best_video = max(video_streams, key=lambda s: s.video_quality.value, default=None)
        best_audio = max(audio_streams, key=lambda s: s.audio_quality.value, default=None)
        return [best_video, best_audio]

    def _save_credential(self):
        """存储哔哩哔哩登录凭证"""
        if self._credential is None:
            return

        self._cookies_file.write_text(json.dumps(self._credential.get_cookies()))

    def _load_credential(self):
        """从文件加载哔哩哔哩登录凭证"""
        if not self._cookies_file.exists():
            return

        self._credential = Credential.from_cookies(json.loads(self._cookies_file.read_text()))

    async def login_with_qrcode(self) -> bytes:
        """通过二维码登录获取哔哩哔哩登录凭证"""
        self._qr_login = QrCodeLogin()
        await self._qr_login.generate_qrcode()

        qr_pic = self._qr_login.get_qrcode_picture()
        return qr_pic.content

    async def check_qr_state(self) -> AsyncGenerator[str]:
        """检查二维码登录状态"""
        scan_tip_pending = True

        for _ in range(30):
            state = await self._qr_login.check_state()
            match state:
                case QrCodeLoginEvents.DONE:
                    yield "登录成功"
                    self._credential = self._qr_login.get_credential()
                    self._save_credential()
                    break
                case QrCodeLoginEvents.CONF:
                    if scan_tip_pending:
                        yield "二维码已扫描, 请确认登录"
                        scan_tip_pending = False
                case QrCodeLoginEvents.TIMEOUT:
                    yield "二维码过期, 请重新生成"
                    break
            await asyncio.sleep(2)
        else:
            yield "二维码登录超时, 请重新生成"

    async def _init_credential(self):
        """初始化哔哩哔哩登录凭证"""
        if pconfig.bili_ck is None:
            self._load_credential()
            return

        credential = Credential.from_cookies(ck2dict(pconfig.bili_ck))
        if await credential.check_valid():
            logger.info(f"`parser_bili_ck` 有效, 保存到 {self._cookies_file}")
            self._credential = credential
            self._save_credential()
        else:
            logger.info(f"`parser_bili_ck` 已过期, 尝试从 {self._cookies_file} 加载")
            self._load_credential()

    @property
    async def credential(self) -> Credential | None:
        """哔哩哔哩登录凭证"""

        if self._credential is None:
            await self._init_credential()
            return self._credential

        if not await self._credential.check_valid():
            logger.warning("哔哩哔哩凭证已过期, 请重新配置")
            return None

        if await self._credential.check_refresh():
            logger.info("哔哩哔哩凭证需要刷新")
            if self._credential.has_ac_time_value() and self._credential.has_bili_jct():
                await self._credential.refresh()
                logger.info(f"哔哩哔哩凭证刷新成功, 保存到 {self._cookies_file}")
                self._save_credential()
            else:
                logger.warning("哔哩哔哩凭证刷新需要包含 `SESSDATA`, `ac_time_value` 项")

        return self._credential
