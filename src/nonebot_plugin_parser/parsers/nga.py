import re
import json
import asyncio
from typing import Any, ClassVar

from .base import Platform, BaseParser, PlatformEnum, handle
from ..config import pconfig
from ..exception import ParseException

# 渲染前 4 楼回复（不含主楼 0 楼）
MAX_REPLY_FLOORS = 4

# NGA 移动端 lite JSON 接口：走 read.php + lite=js&__output=11 返回标准 JSON，
# 配合移动 App UA 可绕过 web 端 guestJs JS 挑战（纯 httpx/curl_cffi 均无法通过）。
NGA_API_URL = "https://bbs.nga.cn/read.php"
NGA_APP_UA = "NGA_skull/7.3.5 (iPhone14,2; iOS 16.5; Scale/3.00)"
NGA_REQUEST_TIMEOUT = 15


class NGAParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.NGA, display_name="NGA")

    def __init__(self):
        super().__init__()
        # 移动 App UA + JSON 接头，绕过 web 端 403 反爬
        extra_headers = {
            "User-Agent": NGA_APP_UA,
            "Referer": "https://bbs.nga.cn/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self.headers.update(extra_headers)

    @staticmethod
    def build_url_by_tid(tid: str | int) -> str:
        return f"https://nga.178.com/read.php?tid={tid}"

    @staticmethod
    def build_img_url(path: str) -> str:
        return "https://img.nga.178.com/attachments" + path

    @handle("nga", r"tid=(?P<tid>\d+)")
    async def _parse(self, searched: re.Match[str]):
        tid = int(searched.group("tid"))
        url = self.build_url_by_tid(tid)

        data = await self._fetch_thread_json(tid)

        d = data.get("data") or {}
        thread = d.get("__T") or {}
        users: dict[str, Any] = d.get("__U") or {}
        rows: list[Any] = d.get("__R") or []
        if not rows or not isinstance(rows, list):
            raise ParseException("JSON 响应缺少 __R 回帖数据")

        # __R[0] 为主楼（0 楼），其后为回复楼层
        main_row = rows[0] if isinstance(rows[0], dict) else {}

        # ── 主楼（0 楼）──────────────────────────────────────────────
        title = thread.get("subject") or main_row.get("subject")
        main_uid = str(main_row.get("authorid") or thread.get("authorid") or "")
        main_author_name = self._lookup_username(users, main_uid)
        author = self.create_author(main_author_name) if main_author_name else None
        # 楼层时间戳：优先 postdatetimestamp(int epoch)，回退 __T.postdate(int)
        timestamp = self._to_int(main_row.get("postdatetimestamp") or thread.get("postdate"))

        main_text_lines, main_image_urls = self._extract_floor_content(main_row.get("content") or "")
        graphics = self._content_to_graphics(main_text_lines, main_image_urls)

        # ── 回复楼层（前 MAX_REPLY_FLOORS 楼）───────────────────────────
        posts = self._extract_reply_posts(rows, users)

        return self.result(
            title=title,
            url=url,
            author=author,
            graphics=graphics,
            timestamp=timestamp,
            extra={"posts": posts, "main_uid": main_uid},
        )

    async def _fetch_thread_json(self, tid: int) -> dict[str, Any]:
        """请求 NGA lite=js JSON 接口并解析为 dict。

        优先 curl_cffi（TLS 指纹模拟），不可用回退 httpx。
        """
        api_url = f"{NGA_API_URL}?tid={tid}&lite=js&__output=11"

        # 优先 curl_cffi（绕过反爬），不可用回退 httpx
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            AsyncSession = None  # type: ignore[assignment]

        if AsyncSession is not None:
            # NGA 在国内，默认不走代理；用户显式配置了代理才走
            proxy_url = pconfig.proxy
            if proxy_url:
                proxies = {"https": proxy_url, "http": proxy_url}
            else:
                proxies = {"https": "", "http": ""}

            async def _do_request():
                async with AsyncSession(
                    impersonate="chrome110",
                    timeout=NGA_REQUEST_TIMEOUT,
                ) as session:
                    return await session.get(
                        api_url,
                        headers=self.headers,
                        allow_redirects=True,
                        proxies=proxies,  # type: ignore[arg-type]
                    )

            try:
                resp = await asyncio.wait_for(_do_request(), timeout=NGA_REQUEST_TIMEOUT)
            except Exception as e:
                raise ParseException(f"请求失败: {e}")
        else:
            # 回退 httpx（大概率被 403，保留兜底）
            resp = await self.request(api_url, follow_redirects=True, raise_for_status=False)

        if resp.status_code != 200:
            raise ParseException(f"无法获取页面, HTTP {resp.status_code}")

        return self._parse_json_payload(resp.text)

    @staticmethod
    def _parse_json_payload(text: str) -> dict[str, Any]:
        """解析 NGA JSON 响应。

        content-type 为 text/json;charset=UTF-8。响应体可能直接是 {...}，
        也可能带 `window.xxx=` 之类的 JS 赋值前缀（NGA 原始变量名有拼写错误）。
        NGA 偶发返回非法 \\uXXXX 转义（如 \\u 后非 4 位 hex），需清洗后再解析。
        """
        body = text.strip()
        # 去掉 `window.xxx=` 前缀，取首个 JSON 对象
        start = body.find("{")
        if start == -1:
            raise ParseException("响应不是合法 JSON")
        blob = body[start:]
        # NGA 偶发非法 \u 转义：把 \u 后非4位hex的转义降级为普通字符，避免 JSONDecodeError
        blob = re.sub(r"\\u(?![0-9a-fA-F]{4})", r"\\\\u", blob)
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            # 兜底：按平衡花括号截取（容忍字符串内花括号被误判，strict=False 容忍控制字符）
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
                raise ParseException("JSON 解析失败: 花括号不平衡")
            try:
                return json.loads(blob[:end], strict=False)
            except json.JSONDecodeError as e:
                raise ParseException(f"JSON 解析失败: {e}")

    @staticmethod
    def _lookup_username(users: dict[str, Any], uid: str) -> str | None:
        """从 __U 用户表查 username，查不到退化为 UID:xxx。"""
        if not uid:
            return None
        # JSON 对象键恒为字符串，按字符串 uid 查表
        info = users.get(uid)
        if isinstance(info, dict):
            name = info.get("username")
            if name:
                return name
        return f"UID:{uid}"

    @staticmethod
    def _to_int(val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def _extract_floor_content(self, content: str) -> tuple[list[str], list[str]]:
        """提取单个楼层正文，返回 (文字行列表, 图片URL列表)。

        NGA content 字段是 BBCode 文本：[img]./mon_xxx[/img] 为内嵌图片，
        <br/> 为换行，[s:ac:表情]/[url]/[quote] 等剥离为纯文本。
        """
        text_lines: list[str] = []
        image_urls: list[str] = []
        # <br/> / <br> → 换行（HTML 解析时 BS4 会做，JSON 接口需手动）
        text = re.sub(r"<br\s*/?>", "\n", content)
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
                if line.strip():
                    text_lines.append(line.strip())
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
        rows: list[Any],
        users: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """提取前 MAX_REPLY_FLOORS 楼回复（rows[0] 为主楼，从 rows[1] 起）。

        images 存放 ImageContent 对象（下载任务），由 ensure_downloads_complete 统一下载，
        模板渲染时通过 .path_uri 取本地 file:// 路径。
        """
        posts: list[dict[str, Any]] = []
        for idx in range(1, len(rows)):
            if len(posts) >= MAX_REPLY_FLOORS:
                break
            row = rows[idx]
            if not isinstance(row, dict):
                continue
            uid = str(row.get("authorid") or "")
            name = self._lookup_username(users, uid) or (f"UID:{uid}" if uid else "匿名")
            timestamp = self._to_int(row.get("postdatetimestamp"))
            text_lines, image_urls = self._extract_floor_content(row.get("content") or "")
            images = [self.create_image_content(url) for url in image_urls]
            posts.append(
                {
                    "floor": self._to_int(row.get("lou")) or idx,
                    "uid": uid or None,
                    "name": name,
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
