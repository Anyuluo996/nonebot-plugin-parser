import re
import json
import time
import random
import asyncio
from typing import ClassVar

from bs4 import Tag, BeautifulSoup
from httpx import HTTPError, AsyncClient
from nonebot import logger

from .base import Platform, BaseParser, PlatformEnum, handle
from ..exception import ParseException


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
        self.base_img_url = "https://img.nga.178.com/attachments"

    @staticmethod
    def nga_url(tid: str | int) -> str:
        return f"https://nga.178.com/read.php?tid={tid}"

    @staticmethod
    def build_url_by_tid(tid: str | int) -> str:
        return f"https://nga.178.com/read.php?tid={tid}"

    # ("ngabbs.com", r"https?://ngabbs\.com/read\.php\?tid=(?P<tid>\d+)(?:[&#A-Za-z\d=_-]+)?"),
    # ("nga.178.com", r"https?://nga\.178\.com/read\.php\?tid=(?P<tid>\d+)(?:[&#A-Za-z\d=_-]+)?"),
    # ("bbs.nga.cn", r"https?://bbs\.nga\.cn/read\.php\?tid=(?P<tid>\d+)(?:[&#A-Za-z\d=_-]+)?"),
    @handle("nga", r"tid=(?P<tid>\d+)")
    async def _parse(self, searched: re.Match[str]):
        tid = searched.group("tid")
        url = self.nga_url(tid)

        async with AsyncClient(headers=self.headers, timeout=self.timeout, follow_redirects=True) as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 403 and "guestJs" in resp.text:
                    logger.debug("第一次请求 403 错误, 包含 guestJs cookie, 重试请求")
                    if matched := re.search(r"document\.cookie\s*=\s*['\"]guestJs=([^;'\"]+)", resp.text):
                        guest_js = matched.group(1)
                        client.cookies.set("guestJs", guest_js, domain=".178.com")
                        await asyncio.sleep(0.3)
                        rand_param = random.randint(0, 999)
                        separator = "&" if "?" in url else "?"
                        retry_url = f"{url}{separator}rand={rand_param}"
                        resp = await client.get(retry_url)

            except HTTPError as e:
                raise ParseException(f"请求失败: {e}")

        if resp.status_code != 200:
            raise ParseException(f"无法获取页面, HTTP {resp.status_code}")

        html = resp.text

        if "需要" in html and ("登录" in html or "请登录" in html):
            raise ParseException("页面可能需要登录后访问")

        soup = BeautifulSoup(html, "html.parser")

        title = None
        title_tag = soup.find(id="postsubject0")
        if title_tag and isinstance(title_tag, Tag):
            title = title_tag.get_text(strip=True)

        author = None
        author_tag = soup.find(id="postauthor0")
        if author_tag and isinstance(author_tag, Tag):
            href = author_tag.get("href", "")
            if matched := re.search(r"[?&]uid=(\d+)", str(href)):
                uid = str(matched.group(1))
                script_pattern = r"commonui\.userInfo\.setAll\s*\(\s*(\{.*?\})\s*\)"
                if matched := re.search(script_pattern, html, re.DOTALL):
                    user_info = matched.group(1)
                    try:
                        user_info = json.loads(user_info)
                        if uid in user_info:
                            author = user_info[uid].get("username")
                    except (json.JSONDecodeError, KeyError):
                        pass

        author = self.create_author(author) if author else None

        timestamp = None
        time_tag = soup.find(id="postdate0")
        if time_tag and isinstance(time_tag, Tag):
            timestr = time_tag.get_text(strip=True)
            timestamp = int(time.mktime(time.strptime(timestr, "%Y-%m-%d %H:%M")))

        text, contents = None, []
        content_tag = soup.find(id="postcontent0")
        if content_tag and isinstance(content_tag, Tag):
            raw_text = content_tag.get_text("\n", strip=True)
            lines = raw_text.split("\n")
            temp_text = ""

            for line in lines:
                if img_matches := re.findall(r"\[img\](.*?)\[/img\]", line):
                    before_img = line.split("[img]")[0]
                    if before_img.strip():
                        temp_text += before_img + "\n"

                    for img_url in img_matches:
                        full_url = self.base_img_url + img_url[1:]
                        clean_desc = self.clean_nga_text(temp_text, max_length=200)
                        contents.append(self.create_graphics_content(full_url, text=clean_desc))
                        temp_text = ""
                elif "[" in line:
                    if clean_line := re.sub(r"\[[^\]]*?\]", "", line).strip():
                        temp_text += clean_line + "\n"
                else:
                    temp_text += line + "\n"

            text = self.clean_nga_text(temp_text)

        return self.result(
            title=title,
            url=url,
            author=author,
            text=text,
            contents=contents,
            timestamp=timestamp,
        )

    @staticmethod
    def clean_nga_text(text: str, max_length: int = 500) -> str:
        rules: list[tuple[str, str, int]] = [
            (r"\[img\][^\[\]]*\[/img\]", "", 0),
            (r"\[img\][^\[\]]*", "", 0),
            (r"\[url=[^\]]*\]([^\[]*?)\[/url\]", r"\1", 0),
            (r"\[url\]([^\[]*?)\[/url\]", r"\1", 0),
            (r"\[quote\].*?\[/quote\]", "", re.DOTALL),
            (r"\[(b|i|u)\](.*?)\[/\1\]", r"\2", re.DOTALL),
            (r"\[(color|size)=[^\]]*\](.*?)\[/\1\]", r"\2", re.DOTALL),
            (r"\[[^]]+\]", "", 0),
            (r"\n{3,}", "\n\n", 0),
            (r"[ \t]+", " ", 0),
            (r"\n\s+\n", "\n\n", 0),
        ]

        for rule in rules:
            pattern, replacement, flags = rule[0], rule[1], rule[2]
            text = re.sub(pattern, replacement, text, flags=flags)

        text = text.strip()

        if len(text) > max_length:
            text = text[:max_length] + "..."

        return text
