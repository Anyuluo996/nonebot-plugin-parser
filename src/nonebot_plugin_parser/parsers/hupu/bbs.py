import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from msgspec import Struct, field
from bs4.element import Tag, NavigableString
from msgspec.json import Decoder

_HUPU_RELATIVE_RE = re.compile(r"(\d+)(天|小时|分钟|秒)前")


def parse_hupu_date(date_str: str) -> int:
    """将虎扑时间字符串解析为时间戳。"""
    date_str = date_str.strip()
    now_dt = datetime.now()
    if date_str == "刚刚":
        return int(now_dt.timestamp())
    if m := _HUPU_RELATIVE_RE.match(date_str):
        value = int(m[1])
        unit = m[2]
        if unit == "天":
            dt = now_dt - timedelta(days=value)
        elif unit == "小时":
            dt = now_dt - timedelta(hours=value)
        elif unit == "分钟":
            dt = now_dt - timedelta(minutes=value)
        else:
            dt = now_dt - timedelta(seconds=value)
        return int(dt.timestamp())
    try:
        year = now_dt.year
        dt = datetime.strptime(f"{year}-{date_str}", "%Y-%m-%d %H:%M")
        return int(dt.timestamp())
    except Exception as e:
        raise ValueError(f"无法解析时间字符串: {date_str!r}") from e


def parse_rich_content(html: str, create_image=None) -> list:
    """HTML → graphics（文本 + 图片）。create_image 为 BaseParser.create_image_content。"""
    soup = BeautifulSoup(html.replace(r"\"", '"'), "html.parser")
    result: list = []
    buffer: list[str] = []

    def flush():
        if buffer:
            text_block = "".join(buffer)
            lines = [line.rstrip() for line in text_block.splitlines()]
            if normalized := "\n".join(lines).strip():
                result.append(normalized)
            buffer.clear()

    for element in soup.descendants:
        if isinstance(element, Tag):
            if element.name in ("p", "br"):
                buffer.append("\n")
                continue
            if element.name == "img":
                src = element.get("src")
                if src and create_image:
                    flush()
                    result.append(create_image(str(src)))
        elif isinstance(element, NavigableString):
            if text := str(element).strip():
                buffer.append(text)

    flush()
    return result


class Forum(Struct):
    fid: str
    f_name: str


class User(Struct):
    puid: str
    username: str
    header: str
    date: str

    @property
    def timestamp(self) -> int:
        return parse_hupu_date(self.date)


class Detail(Struct):
    tid: str
    f_info: Forum
    user: User
    title: str
    html: str = field(name="content")
    hits: str
    replies: str
    lights: str
    via: str

    @property
    def timestamp(self) -> int:
        return self.user.timestamp


class Image(Struct):
    format: str
    src: str


class Reply(Struct):
    pid: str
    user: User
    html: str = field(name="content")
    images: list[Image] | None
    light: str | int
    replies: str | int
    via: str

    @property
    def timestamp(self) -> int:
        return self.user.timestamp


class Data(Struct):
    t_detail: Detail
    r_list: list[Reply]


class BBS(Struct):
    data: Data


decoder = Decoder(BBS)
