import io
import zipfile

from bs4 import BeautifulSoup
from msgspec import Struct
from msgspec.json import Decoder

from .models import File, Time, User


async def fetch_html_text_from_zip(parser, file: File) -> list[str]:
    """从 contentFile 的 zip 中提取正文文本行。

    parser 为 BaseParser 实例，用其 request 获取 zip bytes。
    """
    resp = await parser.request(file.url)
    zip_bytes = resp.content
    html_name = file.filename.replace("_html.zip", "_html.html")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        try:
            html_bytes = zf.read(html_name)
        except KeyError:
            html_name = next(
                (name for name in zf.namelist() if name.lower().endswith(".html")),
                None,
            )
            if not html_name:
                raise RuntimeError("no html file found in content zip")
            html_bytes = zf.read(html_name)
    html = html_bytes.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text("\n", strip=True)
    if not full_text:
        return []
    lines = [line for line in full_text.splitlines() if line.strip()]
    # 去掉第一行（标题，与 title 重复）
    return lines if len(lines) <= 1 else lines[1:]


class DataObject(Struct):
    author: User
    contentFile: File
    publishDate: Time
    modifyDate: Time
    objectId: str
    title: str
    description: str
    readCount: int
    rewardCoin: int
    thumbUpCount: int
    commentCount: int


class ArticleByIdV2(Struct):
    dataObject: DataObject
    msg: str


decoder = Decoder(ArticleByIdV2)
