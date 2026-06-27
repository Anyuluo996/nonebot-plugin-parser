import re
from typing import Any, TypeVar, ClassVar

from msgspec.json import Decoder

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle

T = TypeVar("T")


def _format_num(n: float) -> str:
    """数字格式化：万/亿。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


class ZhiHuParser(BaseParser):
    """知乎解析器：支持专栏文章 / 回答 / 问题三种入口。

    使用知乎官方 v4 API + zse96 请求签名（见 sign.py）。
    正文富内容（图文/视频混排）解析为 graphics。
    """

    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.ZHIHU, display_name="知乎"
    )

    async def fetch(
        self, url: str, decoder: Decoder[T], ext_header: dict[str, Any] | None = None
    ) -> T:
        from .sign import sign_zhihu_fetch_request

        res = await self.request(
            url,
            headers={
                **self.headers,
                **sign_zhihu_fetch_request(url),
                **(ext_header or {}),
            },
        )
        if res.status_code != 200:
            raise ParseException(f"知乎接口返回 {res.status_code}: {res.text[:120]}")
        return decoder.decode(res.content)

    @handle("zhuanlan.zhihu.com", r"zhuanlan\.zhihu\.com/p/(?P<article_id>\d+)")
    async def parse_zhuanlan(self, searched: re.Match[str]):
        from .article import decoder as articleDecoder

        article_id = searched.group("article_id")
        article_data = await self.fetch(
            "https://www.zhihu.com/api/v4/articles/"
            f"{article_id}?include=content,topics,paid_info,can_comment,excerpt,"
            "thanks_count,voteup_count,comment_count,visited_count,relationship,"
            "ip_info,relationship.vote,author.badge_v2",
            articleDecoder,
        )

        statistics = article_data.reaction.statistics

        return self.result(
            title=article_data.title,
            graphics=await article_data.get_content(self),
            timestamp=article_data.updated,
            url=f"https://zhuanlan.zhihu.com/p/{article_data.id}",
            author=self.create_author(
                name=article_data.author.name,
                avatar_url=article_data.author.avatar_url,
                description=article_data.author.headline,
            ),
            extra={
                "info": (
                    f"赞同 {_format_num(statistics.up_vote_count)} | "
                    f"评论 {_format_num(statistics.comment_count)} | "
                    f"收藏 {_format_num(statistics.favorites)}"
                ),
            },
        )

    @handle(
        "www.zhihu.com",
        r"zhihu\.com/question/\d+/answer/(?P<answer_id>\d+)",
    )
    async def parse_answer(self, searched: re.Match[str]):
        from .answer import decoder as answerDecoder

        answer_id = searched.group("answer_id")
        answer_data = await self.fetch(
            "https://www.zhihu.com/api/v4/answers/"
            f"{answer_id}?include=content,paid_info,can_comment,excerpt,thanks_count,"
            "voteup_count,comment_count,visited_count,attachment,reaction,ip_info,"
            "pagination_info,question.topics,reaction.relation.voting,author.badge_v2",
            answerDecoder,
            {"Referer": f"https://www.zhihu.com/question/-/answer/{answer_id}"},
        )

        question = answer_data.question
        statistics = answer_data.reaction.statistics

        return self.result(
            title=question.title,
            graphics=await answer_data.get_content(self),
            timestamp=answer_data.updated_time,
            url=f"https://www.zhihu.com/question/{question.id}/answer/{answer_id}",
            author=self.create_author(
                name=answer_data.author.name,
                avatar_url=answer_data.author.avatar_url,
                description=answer_data.author.headline,
            ),
            extra={
                "info": (
                    f"赞同 {_format_num(statistics.up_vote_count)} | "
                    f"评论 {_format_num(statistics.comment_count)} | "
                    f"收藏 {_format_num(statistics.favorites)}"
                ),
            },
        )

    @handle("www.zhihu.com", r"zhihu\.com/question/(?P<question_id>\d+)(?!/answer)")
    async def parse_question(self, searched: re.Match[str]):
        from .question import decoder as questionDecoder

        question_id = searched.group("question_id")
        question_data = await self.fetch(
            "https://www.zhihu.com/api/v4/questions/"
            f"{question_id}?include=read_count,visit_count,answer_count,voteup_count,"
            "comment_count,follower_count,detail,excerpt,author,relationship.is_following,"
            "topics",
            questionDecoder,
        )

        return self.result(
            title=question_data.title,
            graphics=await question_data.get_content(self),
            timestamp=question_data.updated_time,
            url=f"https://www.zhihu.com/question/{question_id}",
            author=self.create_author(
                name=question_data.author.name,
                avatar_url=question_data.author.avatar_url,
                description=question_data.author.headline,
            ),
            extra={
                "info": (
                    f"浏览 {_format_num(question_data.visit_count)} | "
                    f"回答 {_format_num(question_data.answer_count)} | "
                    f"关注 {_format_num(question_data.follower_count)}"
                ),
            },
        )
