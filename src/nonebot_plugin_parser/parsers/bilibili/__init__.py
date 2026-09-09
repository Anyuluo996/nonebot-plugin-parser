import asyncio
from re import Match
from typing import ClassVar
from collections.abc import AsyncGenerator

from nonebot import logger
from bilibili_api import HEADERS, Credential, select_client, request_settings
from bilibili_api.opus import Opus
from bilibili_api.video import Video

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
from .dynamic import DynamicInfo
from .streams import (
    _is_p2p_node as _is_p2p_node,
)
from .streams import (
    safe_convert,
    detect_best_streams_safe,
)
from .streams import (
    _select_preferred_streams as _select_preferred_streams,
)
from .credential import BilibiliCredentialManager

# 兼容别名: 旧代码/测试从包顶层导入 _safe_convert
_safe_convert = safe_convert

# 转发链递归深度上限，防止循环引用/极深嵌套导致 RecursionError 崩溃
MAX_REPOST_DEPTH = 5

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
        self._credentials = BilibiliCredentialManager(pconfig.config_dir / "bilibili_cookies.json")

    @property
    async def credential(self) -> Credential | None:
        """哔哩哔哩登录凭证（校验有效性并按需刷新）"""
        return await self._credentials.get()

    async def login_with_qrcode(self) -> bytes:
        """通过二维码登录获取哔哩哔哩登录凭证（返回二维码图片内容）"""
        return await self._credentials.login_with_qrcode()

    def check_qr_state(self) -> AsyncGenerator[str]:
        """检查二维码登录状态"""
        return self._credentials.check_qr_state()

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
        # _get_video 已触发过一次凭据校验/刷新, 此处复用结果,
        # 避免同一次解析内对 check_valid/check_refresh 的重复网络请求
        credential = await self.credential
        video_info = safe_convert(await video.get_info(), VideoInfo, context="视频信息")
        # UP
        author = self.create_author(video_info.owner.name, video_info.owner.face)
        # 处理分 p
        page_info = video_info.extract_info_with_page(page_num)

        # 获取 AI 总结
        if credential:
            cid = await video.get_cid(page_info.index)
            ai_conclusion = await video.get_ai_conclusion(cid)
            ai_conclusion = safe_convert(ai_conclusion, AIConclusion, context="AI总结")
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
            v_url, v_backups, a_url, a_backups = await self.extract_download_urls(
                video=video, page_index=page_info.index
            )
            if page_info.duration > pconfig.duration_maximum:
                logger.warning(f"视频时长 {page_info.duration} 秒, 超过 {pconfig.duration_maximum} 秒, 取消下载")
                raise IgnoreException
            if a_url is not None:
                return await self.downloader.download_av_and_merge(
                    v_url,
                    a_url,
                    output_path=output_path,
                    ext_headers=self.headers,
                    v_backup_urls=v_backups,
                    a_backup_urls=a_backups,
                )
            else:
                return await self.downloader.download_file(
                    v_url,
                    file_name=output_path.name,
                    ext_headers=self.headers,
                    backup_urls=v_backups,
                )

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

        dynamic_info = safe_convert(await dynamic.get_info(), DynamicWrapper, context="动态信息").item
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
        opus_data = safe_convert(opus_info, OpusItem, context="图文动态")
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

        room_data = safe_convert(info_dict, RoomData, context="直播信息")
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

        favdata = safe_convert(fav_dict, FavData, context="收藏夹")

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
    ) -> tuple[str, list[str], str | None, list[str]]:
        """解析视频下载链接

        返回 (视频主链, 视频backup列表, 音频主链或None, 音频backup列表)。
        主链已做 P2P 节点过滤(命中 mcdn 等坏节点时从 backup 提取正规 CDN 替换);
        backup 列表保留全部链接供下载层重试时轮换不同 CDN。
        """
        from bilibili_api.video import (
            AudioStreamDownloadURL,
            VideoStreamDownloadURL,
        )

        if video is None:
            video = await self._get_video(bvid=bvid, avid=avid)

        # 获取下载数据（detect_best_streams 崩溃时由 detect_best_streams_safe 降级）
        download_url_data = await video.get_download_url(page_index=page_index)
        streams = detect_best_streams_safe(download_url_data)

        video_stream = streams[0]
        if not isinstance(video_stream, VideoStreamDownloadURL):
            raise DownloadException("未找到可下载的视频流")
        logger.debug(f"视频流质量: {video_stream.video_quality.name}, 编码: {video_stream.video_codecs}")

        # P2P 节点过滤: 主链命中 mcdn 等坏节点时, 从 backup 提取正规 CDN 替换
        v_url, v_backups = _select_preferred_streams(video_stream.url, list(video_stream.backup_url or []))

        audio_stream = streams[1]
        if not isinstance(audio_stream, AudioStreamDownloadURL):
            return v_url, v_backups, None, []
        logger.debug(f"音频流质量: {audio_stream.audio_quality.name}")
        a_url, a_backups = _select_preferred_streams(audio_stream.url, list(audio_stream.backup_url or []))
        return v_url, v_backups, a_url, a_backups
