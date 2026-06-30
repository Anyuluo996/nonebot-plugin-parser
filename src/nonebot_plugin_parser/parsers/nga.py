import re
import json
import time
import random
import asyncio
from typing import Any, ClassVar

from bs4 import Tag, BeautifulSoup
from httpx import HTTPError, AsyncClient
from nonebot import logger

from .base import Platform, BaseParser, PlatformEnum, handle
from ..exception import ParseException

# 渲染前 4 楼回复（不含主楼 0 楼）
MAX_REPLY_FLOORS = 4


class NGAParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.NGA, display_name="NGA")

    def __init__(self):
        super().__init__()
        extra_headers = {
            "Referer": "https://nga.178.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        self.headers.update(extra_headers)

    @staticmethod
    def build_url_by_tid(tid: str | int) -> str:
        return f"https://nga.178.com/read.php?tid={tid}"

    @staticmethod
    def build_img_url(path: str) -> str:
        return "https://img.nga.178.com/attachments" + path

    # ("ngabbs.com", r"https?://ngabbs\.com/read\.php\?tid=(?P<tid>\d+)(?:[&#A-Za-z\d=_-]+)?"),
    # ("nga.178.com", r"https?://nga\.178\.com/read\.php\?tid=(?P<tid>\d+)(?:[&#A-Za-z\d=_-]+)?"),
    # ("bbs.nga.cn", r"https?://bbs\.nga\.cn/read\.php\?tid=(?P<tid>\d+)(?:[&#A-Za-z\d=_-]+)?"),
    @handle("nga", r"tid=(?P<tid>\d+)")
    async def _parse(self, searched: re.Match[str]):
        # 从匹配对象中获取原始URL
        tid = int(searched.group("tid"))
        url = self.build_url_by_tid(tid)

        async with AsyncClient(headers=self.headers, timeout=self.timeout, follow_redirects=True) as client:
            try:
                # 第一次请求可能返回 403，但包含设置 cookie 的 JavaScript
                resp = await client.get(url)
                # 如果返回 403 且包含 guestJs cookie设置，提取cookie并重试
                if resp.status_code == 403 and "guestJs" in resp.text:
                    logger.debug("第一次请求 403 错误, 包含 guestJs cookie, 重试请求")
                    # 从JavaScript中提取 guestJs cookie 值
                    if matched := re.search(r"document\.cookie\s*=\s*['\"]guestJs=([^;'\"]+)", resp.text):
                        guest_js = matched.group(1)
                        client.cookies.set("guestJs", guest_js, domain=".178.com")
                        # 等待一小段时间（模拟 JavaScript 的 setTimeout）
                        await asyncio.sleep(0.3)
                        # 添加随机参数避免缓存（模拟 JavaScript 的行为）
                        rand_param = random.randint(0, 999)
                        separator = "&" if "?" in url else "?"
                        retry_url = f"{url}{separator}rand={rand_param}"

                        # 重试请求
                        resp = await client.get(retry_url)

            except HTTPError as e:
                raise ParseException(f"请求失败: {e}")

        if resp.status_code != 200:
            raise ParseException(f"无法获取页面, HTTP {resp.status_code}")

        # NGA 页面是 GBK 编码，httpx 自动探测可能误判，显式用 gb18030 解码避免乱码
        html = resp.content.decode("gb18030", errors="replace")

        # 简单识别是否需要登录或被拦截
        if "需要" in html and ("登录" in html or "请登录" in html):
            raise ParseException("页面可能需要登录后访问")

        soup = BeautifulSoup(html, "html.parser")

        # 解析 userInfo.setAll()：楼内作者名靠 JS 注入，<a class="author"> 自身无文本，
        # 需要从这段 JS 数据里按 uid 查 username。
        user_info = self._parse_user_info(html)

        # ── 主楼（0 楼）──────────────────────────────────────────────
        title = self._extract_title(soup)
        main_uid, main_author_name = self._extract_author(soup, html, user_info, floor=0)
        author = self.create_author(main_author_name) if main_author_name else None
        timestamp = self._extract_timestamp(soup, floor=0)
        main_content_tag = soup.find(id="postcontent0")
        main_text_lines, main_image_urls = (
            self._extract_floor_content(main_content_tag)
            if main_content_tag and isinstance(main_content_tag, Tag)
            else ([], [])
        )
        graphics = self._content_to_graphics(main_text_lines, main_image_urls)

        # ── 回复楼层（前 MAX_REPLY_FLOORS 楼）───────────────────────────
        posts = self._extract_reply_posts(soup, user_info, html)

        return self.result(
            title=title,
            url=url,
            author=author,
            graphics=graphics,
            timestamp=timestamp,
            extra={"posts": posts, "main_uid": main_uid},
        )

    @staticmethod
    def _parse_user_info(html: str) -> dict[str, dict[str, Any]]:
        """从 commonui.userInfo.setAll({...}) 提取用户信息表，key 为 uid。

        楼内 author <a> 标签无文本（由 JS 注入），真实用户名需从此表按 uid 查询。
        匿名浏览时 username 退化为 "UID:xxx"，avatar 为 null。
        """
        matched = re.search(r"commonui\.userInfo\.setAll\s*\(\s*(\{)", html, re.DOTALL)
        if not matched:
            return {}
        blob = html[matched.start(1) :]
        # 匹配最外层平衡花括号
        depth = 0
        end = 0
        for i, ch in enumerate(blob):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        else:
            return {}
        try:
            # strict=False 容忍 JSON 中的控制字符
            data = json.loads(blob[:end], strict=False)
            return {str(k): v for k, v in data.items()}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _extract_title(self, soup: BeautifulSoup) -> str | None:
        title_tag = soup.find(id="postsubject0")
        if title_tag and isinstance(title_tag, Tag):
            return title_tag.get_text(strip=True)
        return None

    def _extract_author(
        self,
        soup: BeautifulSoup,
        html: str,
        user_info: dict[str, dict[str, Any]],
        floor: int,
    ) -> tuple[str | None, str | None]:
        """提取指定楼层作者 uid 与用户名。

        author <a> 标签无文本，先从 href 取 uid，再从 user_info 表查 username，
        拿不到真实名时退化为 "UID:xxx"。
        """
        author_tag = soup.find(id=f"postauthor{floor}")
        if not author_tag or not isinstance(author_tag, Tag):
            return None, None
        href = str(author_tag.get("href", ""))
        if not (matched := re.search(r"[?&]uid=(\d+)", href)):
            return None, None
        uid = matched.group(1)
        # 先用 a 标签自身文本（部分页面有），否则查 user_info
        name = author_tag.get_text(strip=True) or None
        if not name and uid in user_info:
            name = user_info[uid].get("username")
        if not name:
            name = f"UID:{uid}"
        return uid, name

    def _extract_timestamp(self, soup: BeautifulSoup, floor: int) -> int | None:
        time_tag = soup.find(id=f"postdate{floor}")
        if time_tag and isinstance(time_tag, Tag):
            timestr = time_tag.get_text(strip=True)
            try:
                return int(time.mktime(time.strptime(timestr, "%Y-%m-%d %H:%M")))
            except ValueError:
                return None
        return None

    def _extract_floor_content(self, content_tag: Tag) -> tuple[list[str], list[str]]:
        """提取单个楼层正文，返回 (文字行列表, 图片URL列表)。

        NGA 使用 BBCode：[img]./mon_xxx[/img] 为内嵌图片，[url]/[quote]/[s:表情]
        等剥离为纯文本。
        """
        text_lines: list[str] = []
        image_urls: list[str] = []
        text = content_tag.get_text("\n", strip=True)
        for line in text.split("\n"):
            if "[" in line:
                # [img]./mon_202602/...[/img] 内嵌图片
                if paths := re.findall(r"\[img\]\.(.*?)\[\/img\]", line):
                    for path in paths:
                        image_urls.append(self.build_img_url(path))
                else:
                    # 剥离其他 BBCode 标签（[url][quote][s:ac:哭笑] 等），仅保留文本
                    if clean_line := re.sub(r"\[[^\]]*?\]", "", line).strip():
                        text_lines.append(clean_line)
            else:
                text_lines.append(line)
        return text_lines, image_urls

    def _content_to_graphics(self, text_lines: list[str], image_urls: list[str]) -> list[str | Any]:
        """把楼层正文转为 graphics（文字行 + ImageContent 混排，供 common 渲染降级）。"""
        graphics: list[str | Any] = self.create_empty_graphics()
        for line in text_lines:
            graphics.append(line)
        for img_url in image_urls:
            graphics.append(self.create_image_content(img_url))
        return graphics

    def _extract_reply_posts(
        self,
        soup: BeautifulSoup,
        user_info: dict[str, dict[str, Any]],
        html: str,
    ) -> list[dict[str, Any]]:
        """提取前 MAX_REPLY_FLOORS 楼回复，楼号从 2 开始递增（NGA 楼号 1 为分页占位）。

        images 存放 ImageContent 对象（下载任务），由 ensure_downloads_complete 统一下载，
        模板渲染时通过 .path_uri 取本地 file:// 路径。
        """
        posts: list[dict[str, Any]] = []
        floor = 1
        while len(posts) < MAX_REPLY_FLOORS:
            floor += 1
            content_tag = soup.find(id=f"postcontent{floor}")
            if not content_tag or not isinstance(content_tag, Tag):
                break
            uid, name = self._extract_author(soup, html, user_info, floor)
            timestamp = self._extract_timestamp(soup, floor)
            text_lines, image_urls = self._extract_floor_content(content_tag)
            images = [self.create_image_content(url) for url in image_urls]
            posts.append(
                {
                    "floor": floor,
                    "uid": uid,
                    "name": name or (f"UID:{uid}" if uid else "匿名"),
                    "timestamp": timestamp,
                    "time_text": self._format_timestamp(timestamp),
                    "text": "\n".join(text_lines).strip(),
                    "images": images,
                }
            )
        return posts

    @staticmethod
    def _format_timestamp(ts: int | None) -> str | None:
        if ts is None:
            return None
        from datetime import datetime

        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
