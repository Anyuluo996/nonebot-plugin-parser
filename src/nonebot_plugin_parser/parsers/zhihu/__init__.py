import re
from typing import Any, TypeVar, ClassVar

from msgspec.json import Decoder

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .._format import format_num

T = TypeVar("T")

# 问题页：主楼之外渲染前 N 条高赞回答（feeds/answers 默认时间序，按 voteup 降序取前 N）
TOP_ANSWERS_LIMIT = 3
# feeds/answers 单次最多拉取数（取大一点，以便排序后有足够有效回答）
FEEDS_FETCH_LIMIT = 10


class ZhiHuParser(BaseParser):
    """知乎解析器：支持专栏文章 / 回答 / 问题三种入口。

    数据源：知乎官方 v4 API + zse96 请求签名（见 sign.py）。
    - 问题详情/专栏文章走 ``questions|articles/{id}``（可用）
    - 回答列表/单条回答详情的 ``answers`` 系接口已被风控（40362），
      改用 ``questions/{id}/feeds/answers``（含完整 content，见 feeds.py）

    正文富内容（图文/视频混排）解析为 graphics。
    问题页渲染主楼 + 前 3 条高赞回答（extra["answers"]，专用渲染器画长图）。
    """

    platform: ClassVar[Platform] = Platform(name=PlatformEnum.ZHIHU, display_name="知乎")

    async def fetch(self, url: str, decoder: Decoder[T], ext_header: dict[str, Any] | None = None) -> T:
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
                    f"赞同 {format_num(statistics.up_vote_count)} | "
                    f"评论 {format_num(statistics.comment_count)} | "
                    f"收藏 {format_num(statistics.favorites)}"
                ),
            },
        )

    @handle(
        "www.zhihu.com",
        # 单个正则同时匹配回答(/question/X/answer/Y)与问题(/question/X)，
        # 用可选的 answer 捕获组区分，避免同 keyword 下两个 handler 互相覆盖。
        r"zhihu\.com/question/(?P<question_id>\d+)(?:/answer/(?P<answer_id>\d+))?",
    )
    async def parse_question_or_answer(self, searched: re.Match[str]):
        question_id = searched.group("question_id")
        answer_id = searched.group("answer_id")

        if answer_id is not None:
            return await self._parse_answer(question_id, answer_id)
        return await self._parse_question(question_id)

    async def _parse_answer(self, question_id: str, answer_id: str):
        """回答页：``answers/{id}`` 已风控，改从 feeds/answers 中按 id 匹配目标回答。"""
        from .feeds import decoder as feedDecoder
        from .feeds import build_feeds_answers_url

        resp = await self.fetch(
            build_feeds_answers_url(question_id, limit=FEEDS_FETCH_LIMIT),
            feedDecoder,
            {"Referer": f"https://www.zhihu.com/question/-/answer/{answer_id}"},
        )

        # 按 voteup 降序，便于在 feeds 未直接命中时取最相关
        targets = sorted(
            (c.target for c in resp.data if c.target.id),
            key=lambda t: t.voteup_count,
            reverse=True,
        )

        # 优先精确匹配 answer_id；匹配不到则回退最高赞
        target = next((t for t in targets if t.id == answer_id), None)
        if target is None:
            target = targets[0] if targets else None
        if target is None:
            raise ParseException(f"无法从 feeds/answers 获取回答 {answer_id}（问题 {question_id}）")

        return self.result(
            title=target.question.title or f"知乎回答 {answer_id}",
            graphics=await target.get_content(self),
            timestamp=target.updated_time,
            url=f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}",
            author=self.create_author(
                name=target.author.name,
                avatar_url=target.author.avatar_url,
                description=target.author.headline,
            ),
            extra={
                "info": (f"赞同 {format_num(target.voteup_count)} | 评论 {format_num(target.comment_count)}"),
            },
        )

    async def _parse_question(self, question_id: str):
        """问题页：主楼 detail + 前 3 条高赞回答（feeds/answers）。"""
        from .feeds import decoder as feedDecoder
        from .feeds import build_feeds_answers_url
        from .question import decoder as questionDecoder

        question_data = await self.fetch(
            "https://www.zhihu.com/api/v4/questions/"
            f"{question_id}?include=read_count,visit_count,answer_count,voteup_count,"
            "comment_count,follower_count,detail,excerpt,author,relationship.is_following,"
            "topics",
            questionDecoder,
        )

        graphics = await question_data.get_content(self)

        # 拉取回答并按 voteup 降序取前 N 条，渲染进 extra["answers"]
        answers: list[dict[str, Any]] = []
        try:
            resp = await self.fetch(
                build_feeds_answers_url(question_id, limit=FEEDS_FETCH_LIMIT),
                feedDecoder,
            )
            targets = sorted(
                (c.target for c in resp.data if c.target.id and c.target.content),
                key=lambda t: t.voteup_count,
                reverse=True,
            )
            for t in targets[:TOP_ANSWERS_LIMIT]:
                answers.append(
                    {
                        "name": t.author.name or "匿名用户",
                        "headline": t.author.headline,
                        "avatar": (self.create_image_content(t.author.avatar_url) if t.author.avatar_url else None),
                        "voteup": t.voteup_count,
                        "comment": t.comment_count,
                        "content": await t.get_content(self),
                        "url": f"https://www.zhihu.com/question/{question_id}/answer/{t.id}",
                    }
                )
        except Exception as e:  # feeds 拉取失败不应阻断主楼渲染
            from nonebot import logger

            logger.debug(f"知乎问题 {question_id} 回答拉取失败: {e!r}")

        return self.result(
            title=question_data.title,
            graphics=graphics,
            timestamp=question_data.updated_time,
            url=f"https://www.zhihu.com/question/{question_id}",
            author=self.create_author(
                name=question_data.author.name,
                avatar_url=question_data.author.avatar_url,
                description=question_data.author.headline,
            ),
            extra={
                "info": (
                    f"浏览 {format_num(question_data.visit_count)} | "
                    f"回答 {format_num(question_data.answer_count)} | "
                    f"关注 {format_num(question_data.follower_count)}"
                ),
                "answers": answers,
            },
        )
