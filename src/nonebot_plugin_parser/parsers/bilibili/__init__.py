import json
import asyncio
from re import Match
from typing import ClassVar, Any
from collections.abc import AsyncGenerator
from pathlib import Path

from msgspec import convert
from nonebot import logger
from bilibili_api import HEADERS, Credential, select_client, request_settings
from bilibili_api.opus import Opus
from bilibili_api.video import Video
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents

# 尝试导入 htmlrender
try:
    from nonebot_plugin_htmlrender import get_browser
    HAS_HTMLRENDER = True
except ImportError:
    HAS_HTMLRENDER = False
    logger.warning("未检测到 nonebot_plugin_htmlrender，B站动态将仅使用文本/图片解析模式")

from ..base import (
    DOWNLOADER,
    BaseParser,
    PlatformEnum,
    ParseException,
    DownloadException,
    DurationLimitException,
    handle,
    pconfig,
)
from ..data import Platform, ImageContent, MediaContent
from ..cookie import ck2dict

# 使用 httpx 客户端（curl_cffi 会被重定向到 t.bilibili.com）
# select_client("curl_cffi")
# request_settings.set("impersonate", "chrome131")

# 设置正确的 Referer，避免风控
HEADERS["Referer"] = "https://www.bilibili.com/"
HEADERS["Origin"] = "https://www.bilibili.com"


class BilibiliParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.BILIBILI, display_name="哔哩哔哩")

    def __init__(self):
        self.headers = HEADERS.copy()
        self._credential: Credential | None = None
        self._cookies_file = pconfig.config_dir / "bilibili_cookies.json"

    async def _get_playwright_cookies(self) -> list[dict[str, Any]]:
        """将 API 的 Cookie 转换为 Playwright 格式"""
        cred = await self.credential
        if not cred:
            return []

        cookies_dict = cred.get_cookies()
        pw_cookies = []
        for k, v in cookies_dict.items():
            pw_cookies.append({
                "name": k,
                "value": v,
                "domain": ".bilibili.com",
                "path": "/"
            })
        return pw_cookies

    async def _save_screenshot(self, img_bytes: bytes, content_type: str, content_id: int) -> Path:
        """保存截图到缓存目录并返回路径"""
        cache_dir = pconfig.cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        filename = f"bili_{content_type}_{content_id}.jpg"
        output_path = cache_dir / filename

        # 写入文件
        output_path.write_bytes(img_bytes)
        return output_path

    async def _capture_opus_screenshot(self, opus_id: int) -> bytes:
        """使用 htmlrender 截取图文动态页面"""
        if not HAS_HTMLRENDER:
            raise ImportError("htmlrender not installed")

        url = f"https://m.bilibili.com/opus/{opus_id}"

        # 获取浏览器实例 (复用 htmlrender 的浏览器)
        browser = await get_browser()

        # 创建上下文 (模拟手机端，布局更紧凑)
        context = await browser.new_context(
            viewport={"width": 450, "height": 800},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        )

        try:
            # 注入 Cookie
            cookies = await self._get_playwright_cookies()
            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            # 访问页面
            await page.goto(url, wait_until="networkidle", timeout=20000)

            # 等待核心元素
            try:
                await page.wait_for_selector(".opus-detail", timeout=5000)
            except:
                pass

            # 注入 CSS/JS 清理页面干扰元素
            await page.evaluate("""() => {
                const selectors = [
                    '.m-navbar',          // 顶部导航
                    '.launch-app-btn',    // 底部唤起App
                    '.open-app-float',    // 悬浮按钮
                    '.m-float-openapp',   // 另一种悬浮
                    '.to-app-btn',        // 详情页的去APP按钮
                    '.opus-read-more'     // "阅读更多"按钮(如果有)
                ];
                selectors.forEach(s => {
                    const els = document.querySelectorAll(s);
                    els.forEach(el => el.style.display = 'none');
                });
                // 强制白色背景
                document.body.style.backgroundColor = '#ffffff';
            }""")

            # 滚动加载图片
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)

            # 截图
            return await page.screenshot(full_page=True, type="jpeg", quality=85)

        finally:
            await context.close()

    async def _capture_dynamic_screenshot(self, dynamic_id: int) -> bytes:
        """使用 htmlrender 截取动态页面"""
        if not HAS_HTMLRENDER:
            raise ImportError("htmlrender not installed")

        url = f"https://m.bilibili.com/dynamic/{dynamic_id}"

        # 获取浏览器实例 (复用 htmlrender 的浏览器)
        browser = await get_browser()

        # 创建上下文 (模拟手机端，布局更紧凑)
        context = await browser.new_context(
            viewport={"width": 450, "height": 800},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        )

        try:
            # 注入 Cookie
            cookies = await self._get_playwright_cookies()
            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            # 访问页面
            await page.goto(url, wait_until="networkidle", timeout=20000)

            # 等待核心元素
            try:
                await page.wait_for_selector(".dyn-card", timeout=5000)
            except:
                pass

            # 注入 CSS/JS 清理页面干扰元素
            await page.evaluate("""() => {
                const selectors = [
                    '.m-navbar',          // 顶部导航
                    '.launch-app-btn',    // 底部唤起App
                    '.open-app-float',    // 悬浮按钮
                    '.m-float-openapp',   // 另一种悬浮
                    '.dyn-header',        // 动态页面的顶部头
                    '.to-app-btn',        // 详情页的去APP按钮
                    '.opus-read-more'     // "阅读更多"按钮(如果有)
                ];
                selectors.forEach(s => {
                    const els = document.querySelectorAll(s);
                    els.forEach(el => el.style.display = 'none');
                });
                // 强制白色背景
                document.body.style.backgroundColor = '#ffffff';
            }""")

            # 滚动加载图片
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)

            # 截图
            return await page.screenshot(full_page=True, type="jpeg", quality=85)

        finally:
            await context.close()

    @handle("b23.tv", r"b23\.tv/[A-Za-z\d\._?%&+\-=/#]+")
    @handle("bili2233", r"bili2233\.cn/[A-Za-z\d\._?%&+\-=/#]+")
    async def _parse_short_link(self, searched: Match[str]):
        """解析短链"""
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    @handle("BV", r"^(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle("/BV", r"bilibili\.com(?:/video)?/(?P<bvid>BV[0-9a-zA-Z]{10})(?:\?p=(?P<page_num>\d{1,3}))?")
    async def _parse_bv(self, searched: Match[str]):
        """解析视频信息"""
        bvid = str(searched.group("bvid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(bvid=bvid, page_num=page_num)

    @handle("av", r"^av(?P<avid>\d{6,})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle("/av", r"bilibili\.com(?:/video)?/av(?P<avid>\d{6,})(?:\?p=(?P<page_num>\d{1,3}))?")
    async def _parse_av(self, searched: Match[str]):
        """解析视频信息"""
        avid = int(searched.group("avid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(avid=avid, page_num=page_num)

    @handle("/dynamic/", r"(?:m\.)?bilibili\.com/dynamic/(?P<dynamic_id>\d+)")
    @handle("t.bili", r"t\.bilibili\.com/(?P<dynamic_id>\d+)")
    async def _parse_dynamic(self, searched: Match[str]):
        """解析动态信息"""
        dynamic_id = int(searched.group("dynamic_id"))
        return await self.parse_dynamic(dynamic_id)

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

    @handle("/read/", r"(?:m\.)?bilibili\.com/read/cv(?P<read_id>\d+)")
    async def _parse_read(self, searched: Match[str]):
        """解析专栏信息"""
        read_id = int(searched.group("read_id"))
        return await self.parse_read_with_opus(read_id)

    @handle("/opus/", r"(?:m\.)?bilibili\.com/opus/(?P<opus_id>\d+)")
    async def _parse_opus(self, searched: Match[str]):
        """解析图文动态信息"""
        opus_id = int(searched.group("opus_id"))
        try:
            return await self.parse_opus(opus_id)
        except Exception as e:
            from bilibili_api.exceptions import ArgsException
            if isinstance(e, ArgsException):
                # Opus 接口解析失败，降级使用 Dynamic 接口
                logger.info(f"Opus 接口解析失败 {opus_id}，尝试使用 Dynamic 接口")
                return await self.parse_dynamic(opus_id)
            raise

    async def parse_video(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_num: int = 1,
    ):
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_num (int): 页码
        """

        from .video import VideoInfo, AIConclusion

        video = await self._get_video(bvid=bvid, avid=avid)
        # 转换为 msgspec struct
        video_info = convert(await video.get_info(), VideoInfo)
        # 获取简介
        text = f"简介: {video_info.desc}" if video_info.desc else None
        # up
        author = self.create_author(video_info.owner.name, video_info.owner.face)
        # 处理分 p
        page_info = video_info.extract_info_with_page(page_num)

        # 获取 AI 总结
        if self._credential:
            cid = await video.get_cid(page_info.index)
            ai_conclusion = await video.get_ai_conclusion(cid)
            ai_conclusion = convert(ai_conclusion, AIConclusion)
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
                raise DurationLimitException
            if a_url is not None:
                return await DOWNLOADER.download_av_and_merge(
                    v_url, a_url, output_path=output_path, ext_headers=self.headers
                )
            else:
                return await DOWNLOADER.streamd(v_url, file_name=output_path.name, ext_headers=self.headers)

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
            text=text,
            author=author,
            contents=[video_content],
            extra={"info": ai_summary},
        )

    async def parse_dynamic(self, dynamic_id: int):
        """解析动态信息

        策略: 优先使用浏览器截图 -> 失败则回退到原解析逻辑

        Args:
            dynamic_id (int): 动态 ID
        """
        from bilibili_api.dynamic import Dynamic

        from .dynamic import DynamicData, DynamicInfo

        dynamic = Dynamic(dynamic_id, await self.credential)

        # 原项目新增：is_article() 检测
        if await dynamic.is_article():
            return await self._parse_bilibili_api_opus(dynamic.turn_to_opus())

        raw_data = await dynamic.get_info()

        # msgspec 转换主数据
        dynamic_data = convert(raw_data, DynamicData)

        # 当前动态信息 (用于标题、作者等基础信息)
        current_info = dynamic_data.item

        # 作者信息始终显示当前动态的作者
        author = self.create_author(current_info.name, current_info.avatar)

        # 尝试构建标题
        title = current_info.title
        if not title:
            # 尝试从描述中截取标题
            desc = current_info.desc_text or current_info.text or ""
            title = desc[:30].replace("\n", " ") + "..." if desc else f"{author.name} 的动态"

        # 尝试截图渲染路径
        screenshot_success = False
        contents = []
        text = None

        if HAS_HTMLRENDER:
            try:
                logger.debug(f"尝试使用浏览器截图渲染动态: {dynamic_id}")
                img_bytes = await self._capture_dynamic_screenshot(dynamic_id)
                img_path = await self._save_screenshot(img_bytes, "dynamic", dynamic_id)
                contents.append(ImageContent(img_path))
                screenshot_success = True
                # 截图模式下，文本置空，因为内容都在图里了
                text = None
            except Exception as e:
                logger.error(f"动态截图失败，将回退到普通解析模式: {e}")
                screenshot_success = False
        else:
            logger.debug("未启用 htmlrender，使用普通解析模式")

        # 回退路径 (Original Logic)
        if not screenshot_success:
            # --- 以下是原本的解析逻辑 ---

            # 手动处理 orig 字段（msgspec 可能无法正确转换嵌套的 orig）
            orig_info: DynamicInfo | None = dynamic_data.orig
            if raw_data.get('item', {}).get('orig') and not orig_info:
                try:
                    orig_info = convert(raw_data['item']['orig'], DynamicInfo)
                except Exception as e:
                    logger.warning(f"手动转换 orig 数据失败: {e}")

            # 默认内容来源是当前动态
            content_source = current_info

            # 文本处理
            text = current_info.text  # 如果是转发，这里通常是转发评论；如果是原创，这里是正文

            is_forward = orig_info is not None
            if is_forward:
                # 如果是转发，图片/媒体内容来源于原动态
                content_source = orig_info

                # 构建文本：转发评论 + 分割线 + 原动态作者/内容
                forward_comment = current_info.desc_text or "转发动态"

                # 获取原动态内容 (优先取 text，没有则取 desc_text)
                orig_text = orig_info.text or orig_info.desc_text or "[原动态内容为空或无法解析]"

                text = f"{forward_comment}\n\n---\n\n@{orig_info.name}：\n{orig_text}"

            # 如果之前没有提取到标题，这里再尝试一次基于文本的标题提取
            if not title or title == f"{author.name} 的动态":
                preview_text = text.replace("\n", " ") if text else ""
                title = preview_text[:30] + "..." if len(preview_text) > 30 else preview_text

            # 下载图片 (从 content_source 获取，这样转发动态就能下载原动态的图)
            for image_url in content_source.image_urls:
                img_task = DOWNLOADER.download_img(image_url, ext_headers=self.headers)
                contents.append(ImageContent(img_task))

            # 如果是转发且原动态被删或者是空内容
            if not contents and is_forward and not orig_info.text:
                 text += "\n[原动态可能已被删除或暂不支持解析]"

        return self.result(
            title=title or "Bilibili 动态",
            text=text,
            timestamp=current_info.timestamp,
            author=author,
            contents=contents,
            url=f"https://t.bilibili.com/{dynamic_id}"
        )

    async def parse_opus(self, opus_id: int):
        """解析图文动态信息

        策略: 优先使用浏览器截图 -> 失败则回退到原解析逻辑

        Args:
            opus_id (int): 图文动态 id
        """
        opus = Opus(opus_id, await self.credential)

        # 尝试截图渲染路径
        screenshot_success = False
        title = None
        author = None
        timestamp = None
        contents = []
        text = None

        if HAS_HTMLRENDER:
            try:
                logger.debug(f"尝试使用浏览器截图渲染图文动态: {opus_id}")
                img_bytes = await self._capture_opus_screenshot(opus_id)
                img_path = await self._save_screenshot(img_bytes, "opus", opus_id)
                contents.append(ImageContent(img_path))
                screenshot_success = True

                # 从 API 获取基础元数据（标题、作者等）
                from .opus import OpusItem
                opus_info = await opus.get_info()
                if isinstance(opus_info, dict):
                    opus_data = convert(opus_info, OpusItem)
                    title = opus_data.title
                    author = self.create_author(*opus_data.name_avatar)
                    timestamp = opus_data.timestamp
            except Exception as e:
                logger.error(f"图文动态截图失败，将回退到普通解析模式: {e}")
                screenshot_success = False
        else:
            logger.debug("未启用 htmlrender，使用普通解析模式")

        # 回退路径 (Original Logic)
        if not screenshot_success:
            return await self._parse_opus_obj(opus)

        return self.result(
            title=title,
            author=author,
            timestamp=timestamp,
            contents=contents,
            text=text,
            url=f"https://m.bilibili.com/opus/{opus_id}"
        )

    async def parse_read_with_opus(self, read_id: int):
        """解析专栏信息, 使用 Opus 接口

        Args:
            read_id (int): 专栏 id
        """
        from bilibili_api.article import Article

        article = Article(read_id)
        return await self._parse_opus_obj(await article.turn_to_opus())

    async def _parse_opus_obj(self, bili_opus: Opus):
        """解析图文动态信息

        Args:
            opus_id (int): 图文动态 id

        Returns:
            ParseResult: 解析结果
        """

        from .opus import OpusItem, TextNode, ImageNode

        opus_info = await bili_opus.get_info()
        if not isinstance(opus_info, dict):
            raise ParseException("获取图文动态信息失败")
        # 转换为结构体
        opus_data = convert(opus_info, OpusItem)
        logger.debug(f"opus_data: {opus_data}")
        author = self.create_author(*opus_data.name_avatar)

        # 按顺序处理图文内容（参考 parse_read 的逻辑）
        contents: list[MediaContent] = []
        current_text = ""

        for node in opus_data.gen_text_img():
            if isinstance(node, ImageNode):
                contents.append(self.create_graphics_content(node.url, current_text.strip(), node.alt))
                current_text = ""
            elif isinstance(node, TextNode):
                current_text += node.text

        return self.result(
            title=opus_data.title,
            author=author,
            timestamp=opus_data.timestamp,
            contents=contents,
            text=current_text.strip(),
        )

    async def parse_live(self, room_id: int):
        """解析直播信息

        Args:
            room_id (int): 直播 id

        Returns:
            ParseResult: 解析结果
        """
        from bilibili_api.live import LiveRoom

        from .live import RoomData

        room = LiveRoom(room_display_id=room_id, credential=await self.credential)
        info_dict = await room.get_room_info()

        room_data = convert(info_dict, RoomData)
        contents: list[MediaContent] = []
        # 下载封面
        if cover := room_data.cover:
            cover_task = DOWNLOADER.download_img(cover, ext_headers=self.headers)
            contents.append(ImageContent(cover_task))

        # 下载关键帧
        if keyframe := room_data.keyframe:
            keyframe_task = DOWNLOADER.download_img(keyframe, ext_headers=self.headers)
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
        """解析收藏夹信息

        Args:
            fav_id (int): 收藏夹 id

        Returns:
            list[GraphicsContent]: 图文内容列表
        """
        from bilibili_api.favorite_list import get_video_favorite_list_content

        from .favlist import FavData

        # 只会取一页，20 个
        fav_dict = await get_video_favorite_list_content(fav_id)

        if fav_dict["medias"] is None:
            raise ParseException("收藏夹内容为空, 或被风控")

        favdata = convert(fav_dict, FavData)

        return self.result(
            title=favdata.title,
            timestamp=favdata.timestamp,
            author=self.create_author(favdata.info.upper.name, favdata.info.upper.face),
            contents=[self.create_graphics_content(fav.cover, fav.desc) for fav in favdata.medias],
        )

    async def _get_video(self, *, bvid: str | None = None, avid: int | None = None) -> Video:
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
        """
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
        """解析视频下载链接

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_index (int): 页索引 = 页码 - 1
        """

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
        streams = detecter.detect_best_streams(
            video_max_quality=pconfig.bili_video_quality,
            codecs=pconfig.bili_video_codes,
            no_dolby_video=True,
            no_hdr=True,
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
