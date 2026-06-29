"""知乎 question feeds/answers 接口的数据模型与拉取。

背景：``api/v4/answers/{id}`` 与 ``api/v4/questions/{id}/answers`` 已被知乎
风控（40362），但 ``api/v4/questions/{id}/feeds/answers`` 仍可用，且通过正确的
``include`` 语法（逗号分隔，不要分号）可一次性拿到完整 ``content`` HTML。

本模块封装该接口的 msgspec Struct 与 include 串。
"""

from typing import TYPE_CHECKING, Any

from msgspec import Struct, field
from msgspec.json import Decoder

from .util import parse_rich_content

if TYPE_CHECKING:
    from ..base import BaseParser


class FeedAuthor(Struct):
    """feeds/answers 里 author 节点的精简模型。"""

    name: str = ""
    headline: str = ""
    """一句话简介"""
    avatar_url: str = ""
    url_token: str = ""


class FeedQuestion(Struct):
    """feeds/answers target.question 的精简模型。"""

    id: str = ""
    title: str = ""


class FeedAnswerTarget(Struct):
    """feeds/answers 单条 answer（card.target）的精简模型。"""

    id: str = ""
    content: str = ""
    """正文 HTML"""
    voteup_count: int = 0
    comment_count: int = 0
    updated_time: int = 0
    created_time: int = 0
    author: FeedAuthor = field(default_factory=FeedAuthor)
    question: FeedQuestion = field(default_factory=FeedQuestion)

    async def get_content(self, parser: "BaseParser") -> list[str | Any]:
        return await parse_rich_content(parser, self.content, "answer")


class FeedCard(Struct):
    """feeds/answers 返回的单个 card。"""

    type: str = ""
    target_type: str = ""
    target: FeedAnswerTarget = field(default_factory=FeedAnswerTarget)


class FeedAnswersResp(Struct):
    """feeds/answers 顶层响应（仅取 data）。"""

    data: list[FeedCard] = field(default_factory=list)


decoder = Decoder(FeedAnswersResp)

# include 串：逗号分隔（不要分号，分号会让 content 不返回）。
# 含 content + 统计 + author + question，足够排序与渲染。
INCLUDE_ANSWERS = (
    "data[*].is_normal,content,voteup_count,comment_count,updated_time,"
    "created_time,author.badge_v2,author.name,author.headline,"
    "author.avatar_url,author.url_token,question.id,question.title"
)


def build_feeds_answers_url(question_id: str, limit: int, offset: int = 0) -> str:
    """构造 feeds/answers 请求 URL。"""
    return (
        f"https://www.zhihu.com/api/v4/questions/{question_id}/feeds/answers"
        f"?include={INCLUDE_ANSWERS}&limit={limit}&offset={offset}"
    )
