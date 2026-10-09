"""X (Twitter) 解析 — GraphQL 直连

主接口直接请求 X 的 GraphQL ``TweetResultByRestId``，鉴权用 web 端内置 Bearer：
未配置 cookie 时走游客 token（guest token，2h TTL 内复用）；配置 ``parser_x_ck``
（浏览器导出的 auth_token/ct0）时带登录态请求，可提升限流额度。

主链失败自动回退第三方源：fxtwitter -> vxtwitter（兜底源仅保证正文/媒体，
无 Article 富文本、链接卡片与投票）。网络层瞬时错误（超时/连接重置）重试一次，
全部异常统一转 ParseException，保证回退链不被网络异常穿透。

GraphQL 的 query id 由 X 前端发布周期轮转，失效时表现为主链 4xx 落入兜底链，
功能退化但不中断。

移植自 nonebot-plugin-parser-suan（其参考 nonebot-plugin-parser-lite），
Phase 1 范围：不含 Grok 翻译 / 互动数据面板 / 粉丝数（需卡片模板配套，留待二期）；
投票推文以纯文本形式并入正文。

支持的形态：普通推文 / 图片 / 视频 / 动图 / 长文本(note_tweet) / X Article /
链接卡片 / 投票 / 引用推文(带评论转发) / 直接转发 / 敏感内容(possibly_sensitive)。
"""

from re import Match
from time import monotonic
from uuid import uuid4
from typing import Any, Literal, ClassVar
from asyncio import sleep
from collections.abc import Callable

from httpx import (
    HTTPError,
    ReadError,
    AsyncClient,
    ReadTimeout,
    ConnectError,
    ProtocolError,
    ConnectTimeout,
)
from msgspec import Struct, field, convert
from nonebot import logger
from msgspec.json import Decoder, encode

from .util import LinkCardData, parse_poll, parse_link_card
from ..base import BaseParser, PlatformEnum, ParseException, handle
from ..data import Platform, ParseResult, MediaContent
from .model import Poll, FxTweet, FxResponse, TweetEntry
from ..cookie import ck2dict
from ...config import pconfig

TWEET_RESULT_API: str = "https://x.com/i/api/graphql/Xl0tsHf4AzflMRjbw9e70A/TweetResultByRestId"
"""主接口: 按推文 id 取推文详情 (GraphQL)"""

GUEST_ACTIVATE_API: str = "https://api.x.com/1.1/guest/activate.json"
"""游客 token 激活接口"""

FALLBACK_API_URL: str = "https://api.fxtwitter.com/Twitter/status/{tid}"
"""备用接口: 只需要推文 id, 直接拼在路径里"""

VX_API_PREFIX: str = "https://api.vxtwitter.com"
"""保底接口: 与 fxtwitter 同为第三方前端, 请求头 UA 规则与 fxtwitter 相反"""

GUEST_TOKEN_TTL: float = 2 * 60 * 60
"""游客 token 有效期(秒), 超过后重新激活"""

TRANSIENT_ERRORS: tuple[type[HTTPError], ...] = (
    ConnectError,
    ConnectTimeout,
    ReadTimeout,
    ReadError,
    ProtocolError,
)
"""瞬时网络错误 (连接被重置/超时等), 值得重试"""

TRANSIENT_RETRY: int = 1
"""瞬时网络错误的重试次数"""

TRANSIENT_RETRY_DELAY: float = 1.0
"""瞬时网络错误重试前的等待(秒)"""

MAX_REPOST_DEPTH: int = 5
"""转发链递归深度上限，防止循环引用/极深嵌套导致 RecursionError 崩溃"""

V2_BEARER: str = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
"""X web 端内置 Bearer"""

FEATURES: bytes = encode(
    {
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "premium_content_api_read_enabled": False,
        "communities_web_enable_tweet_community_results_fetch": True,
        "c9s_tweet_anatomy_moderator_badge_enabled": True,
        "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
        "responsive_web_grok_analyze_post_followups_enabled": True,
        "rweb_cashtags_composer_attachment_enabled": True,
        "responsive_web_jetfuel_frame": True,
        "rweb_sports_post_context_enabled": True,
        "responsive_web_grok_share_attachment_enabled": True,
        "responsive_web_grok_annotations_enabled": True,
        "articles_preview_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "rweb_conversational_replies_downvote_enabled": False,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "content_disclosure_indicator_enabled": True,
        "content_disclosure_ai_generated_indicator_enabled": True,
        "responsive_web_grok_show_grok_translated_post": True,
        "responsive_web_grok_analysis_button_from_backend": True,
        "post_ctas_fetch_enabled": False,
        "rweb_cashtags_enabled": True,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": False,
        "profile_label_improvements_pcf_label_in_post_enabled": True,
        "responsive_web_profile_redirect_enabled": True,
        "rweb_tipjar_consumption_enabled": False,
        "verified_phone_label_enabled": False,
        "responsive_web_nested_quote_preview_enabled": False,
        "responsive_web_grok_image_annotation_enabled": True,
        "responsive_web_grok_imagine_annotation_enabled": True,
        "responsive_web_grok_community_note_auto_translation_is_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
    }
)
"""GraphQL features 参数"""

FIELD_TOGGLES: bytes = encode(
    {
        "withArticleRichContentState": True,
        "withArticlePlainText": False,
        "withArticleSummaryText": True,
        "withArticleVoiceOver": True,
    }
)
"""GraphQL fieldToggles 参数"""

# 两家第三方 API 的 Cloudflare 人机验证规则相反(2026-09-23 实测):
# - vxtwitter: 数据中心出口 IP + 浏览器 UA 判定为伪装浏览器, 弹 "Just a moment"(403);
#   如实声明 httpx 默认 UA 反而放行
# - fxtwitter: 反 challenge 脚本 UA(python-httpx 等被拦), 浏览器 UA 或官方 FxTwitter UA 放行
VX_API_HEADERS = {"User-Agent": "python-httpx"}
FX_API_HEADERS = {"User-Agent": "FxTwitter/1.0"}


class MediaElement(Struct):
    """vxtwitter 媒体项"""

    type: Literal["video", "image", "gif"]
    url: str
    altText: str | None = None
    thumbnail_url: str | None = None
    duration_millis: int | None = None


class VxTwitterResponse(Struct):
    """vxtwitter 响应 (保底源, 仅取正文/媒体/转发)"""

    article: str | None = None
    date_epoch: int = 0
    text: str = ""
    user_name: str = ""
    user_screen_name: str = ""
    user_profile_image_url: str = ""
    qrt: "VxTwitterResponse | None" = None
    media_extended: list[MediaElement] = field(default_factory=list)


fx_decoder = Decoder(FxResponse)
"""fxtwitter 响应解码器"""

vx_decoder = Decoder(VxTwitterResponse)
"""vxtwitter 响应解码器"""


class TwitterParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.TWITTER, display_name="小蓝鸟")

    def __init__(self):
        super().__init__()
        self.headers.update({"Authorization": V2_BEARER})
        self.cookies: dict[str, str] | None = None
        if ck := pconfig.x_ck:
            try:
                self.cookies = ck2dict(ck)
            except ValueError:
                logger.warning("X cookies 格式异常, 已忽略")
        # 游客 token 用实例状态, 避免多个 parser 实例互相干扰
        self.guest_token: str | None = None
        self.guest_token_created_at: float = 0.0

    # ------------------------------------------------------------------
    # 鉴权: 优先 cookie, 否则走游客 token
    # ------------------------------------------------------------------

    @property
    def _has_cookies(self) -> bool:
        """是否配置了登录 cookie"""
        return bool(self.cookies and self.cookies.get("auth_token"))

    def _get_csrf_token(self) -> str:
        """CSRF token: 优先复用 cookie 里的 ct0 (与真实浏览器一致), 否则随机生成"""
        ct0 = (self.cookies or {}).get("ct0", "").strip()
        # 含分隔符/换行的值会破坏请求头, 退回随机值
        if ct0 and not any(ch in ct0 for ch in ";\r\n"):
            return ct0
        return uuid4().hex

    async def _ensure_guest_token(self, client: AsyncClient) -> str:
        """激活游客 token (带 TTL 缓存)"""
        response = await client.post(GUEST_ACTIVATE_API)
        try:
            response.raise_for_status()
            token = response.json().get("guest_token")
            if not isinstance(token, str) or not token:
                raise ValueError("guest_token missing")
        except Exception as e:
            raise ParseException(f"获取游客 token 失败: {response.text[:120]}") from e

        self.guest_token = token
        self.guest_token_created_at = monotonic()
        return token

    async def _guest_token(self, client: AsyncClient) -> str:
        """游客 token 超过 TTL 后重新激活"""
        if self.guest_token is None or monotonic() - self.guest_token_created_at >= GUEST_TOKEN_TTL:
            return await self._ensure_guest_token(client)
        return self.guest_token

    async def _get_auth_headers(self, client: AsyncClient) -> dict[str, str]:
        """构造 GraphQL 请求头 (游客 token 或登录 cookie)"""
        csrf_token = self._get_csrf_token()
        headers = {
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "zh-cn",
            "x-csrf-token": csrf_token,
        }
        if self._has_cookies:
            cookies = self.cookies or {}
            headers["Cookie"] = f"auth_token={cookies.get('auth_token', '')}; ct0={csrf_token};"
            headers["x-twitter-auth-type"] = "OAuth2Session"
        else:
            headers["x-guest-token"] = await self._guest_token(client)
        return headers

    def _client(self) -> AsyncClient:
        return AsyncClient(
            headers=self.headers,
            proxy=pconfig.proxy,
            timeout=self.timeout,
            verify=pconfig.verify_ssl,
        )

    # ------------------------------------------------------------------
    # 内容构建
    # ------------------------------------------------------------------

    def _create_image(self, url: str):
        """model 层注入的图片构建器"""
        return self.create_image_content(url)

    def _create_video(
        self,
        url: str,
        cover_url: str | None = None,
        duration: float = 0.0,
        is_gif: bool = False,
    ):
        """model 层注入的视频构建器 (动图走 gif 转换)"""
        if is_gif:
            contents = self.create_dynamic_contents([url], convert_to_gif=True, cover_url=cover_url)
            return contents[0]
        return self.create_video_content(url, cover_url, duration)

    @staticmethod
    def _poll_text(poll: Poll) -> str:
        """投票转纯文本, 并入正文展示 (卡片模板投票栏留待二期)"""
        lines = [f"📊 投票（共 {poll.total_votes} 票）"]
        for choice in poll.choices:
            lines.append(f"• {choice.label} {choice.percent}%（{choice.count} 票）")
        return "\n".join(lines)

    def _get_link_card(self, tweet: Any) -> LinkCardData | None:
        """推文链接卡片"""
        return parse_link_card(tweet.card)

    def _card_text(self, card: LinkCardData) -> str:
        """卡片的文字信息 (标题 / 站点 / 链接)"""
        lines = [card.title]
        if card.site_name:
            lines.append(card.site_name)
        lines.append(card.url)
        return "\n".join(lines)

    async def _collect_result(
        self,
        raw: TweetEntry,
        is_repost: bool = False,
        depth: int = 0,
    ) -> ParseResult:
        tweet = raw.result.as_tweet
        legacy = tweet.legacy
        card = None if is_repost else self._get_link_card(tweet)

        user = tweet.core.user_results.result
        author = self.create_author(
            name=user.core.name,
            avatar_url=user.avatar_url,
            description=user.description,
        )

        # 本仓库的 graphics 是"图文"插槽 (文本 + 图片交错), 渲染时会替换正文区域,
        # 因此只在 Article 正文这类必须交错的场景使用; 普通正文走 text, 媒体走 contents。
        article_content = tweet.get_article_content(self._create_image, self._create_video)
        if article_content:
            # 保序: 文本段与内嵌媒体按原文交错, 模板按顺序渲染 (拼一块会毁掉图文排版)
            graphics: list[str | Any] = list(article_content)
            # Article 正文已包含 legacy 文本, 不再走 text 渲染
            text: str | None = None
        elif card is not None and card.preview_url:
            # 卡片推文正文很短, 用卡片预览图占据图文区域, 卡片文字并入正文
            graphics = [self._create_image(card.preview_url)]
            text = self._card_text(card)
        else:
            graphics = []
            text = tweet.get_text()

        contents: list[MediaContent] = tweet.get_medias(self._create_image, self._create_video)

        # 投票以纯文本并入正文 (二期再上卡片投票栏)
        if poll := parse_poll(tweet.card):
            text = f"{text}\n\n{self._poll_text(poll)}" if text else self._poll_text(poll)

        # 被引用 / 被转发的推文
        repost: ParseResult | None = None
        repost_status = tweet.quoted_status_result or tweet.retweeted_status_result
        if not is_repost and repost_status is not None and depth < MAX_REPOST_DEPTH:
            repost = await self._collect_result(repost_status, True, depth + 1)

        return self.result(
            author=author,
            title=tweet.title,
            text=text,
            graphics=graphics,
            contents=contents,
            timestamp=legacy.time_local,
            url=f"https://x.com/{user.core.screen_name}/status/{tweet.rest_id}",
            repost=repost,
        )

    # ------------------------------------------------------------------
    # 备用接口: api.fxtwitter.com
    # ------------------------------------------------------------------

    def _collect_fallback(
        self,
        tweet: FxTweet,
        is_repost: bool = False,
        depth: int = 0,
    ) -> ParseResult:
        author = self.create_author(
            name=tweet.author.name,
            avatar_url=tweet.author.avatar_url,
            description=tweet.author.description,
        )

        # 与主接口一致: 图片 / 视频都走 contents (模板按图片网格渲染);
        # graphics 是"图文插槽", 每项会铺满纵向堆叠 (文章式排版), 普通推文不能用。
        contents: list[MediaContent] = []
        for media in tweet.media.all if tweet.media else []:
            if media.type == "photo":
                contents.append(self.create_image_content(media.url))
                continue
            if video_url := media.best_video_url:
                contents.append(
                    self._create_video(
                        video_url,
                        media.thumbnail_url,
                        duration=media.duration_seconds or 0.0,
                        is_gif=media.type == "gif",
                    )
                )

        repost: ParseResult | None = None
        if not is_repost and tweet.quote is not None and depth < MAX_REPOST_DEPTH:
            repost = self._collect_fallback(tweet.quote, True, depth + 1)

        text = tweet.text
        if tweet.poll is not None and tweet.poll.choices:
            text = (
                f"{text}\n\n{self._poll_text(tweet.poll.to_poll())}" if text else self._poll_text(tweet.poll.to_poll())
            )

        return self.result(
            author=author,
            text=text,
            contents=contents,
            timestamp=tweet.created_timestamp,
            url=tweet.url or f"https://x.com/{tweet.author.screen_name}/status/{tweet.id}",
            repost=repost,
        )

    async def _parse_by_fallback(self, tweet_id: str) -> ParseResult:
        """按推文 id 走 fxtwitter 解析"""
        async with self._client() as client:
            response = await client.get(FALLBACK_API_URL.format(tid=tweet_id), headers=FX_API_HEADERS)

            if response.status_code >= 400:
                # 接口用 404 表示推文不存在/不可见
                raise ParseException(f"备用接口获取数据失败 {response.status_code}")

            data = fx_decoder.decode(response.content)
            if data.code != 200 or data.tweet is None:
                raise ParseException(f"解析失败: {data.message or data.code}")
            return self._collect_fallback(data.tweet)

    # ------------------------------------------------------------------
    # 保底接口: api.vxtwitter.com (仅正文/媒体/转发)
    # ------------------------------------------------------------------

    def _collect_vx(self, data: VxTwitterResponse, depth: int = 0) -> ParseResult:
        author = self.create_author(data.user_name, data.user_profile_image_url)

        contents: list[MediaContent] = []
        for media in data.media_extended:
            if media.type == "video":
                contents.append(self.create_video_content(media.url, media.thumbnail_url))
            elif media.type == "gif":
                contents.extend(
                    self.create_dynamic_contents(
                        [media.url],
                        convert_to_gif=True,
                        cover_url=media.thumbnail_url,
                    )
                )
            elif media.type == "image":
                contents.append(self.create_image_content(media.url))

        repost: ParseResult | None = None
        if data.qrt and depth < MAX_REPOST_DEPTH:
            repost = self._collect_vx(data.qrt, depth + 1)

        return self.result(
            author=author,
            text=data.text,
            contents=contents,
            timestamp=data.date_epoch,
            repost=repost,
        )

    async def _parse_by_vxtwitter(self, tweet_id: str) -> ParseResult:
        """按推文 id 走 vxtwitter 解析 (保底)"""
        async with self._client() as client:
            response = await client.get(f"{VX_API_PREFIX}/Twitter/status/{tweet_id}", headers=VX_API_HEADERS)

            if response.status_code >= 400:
                raise ParseException(f"保底接口获取数据失败 {response.status_code}")

            data = vx_decoder.decode(response.content)
            return self._collect_vx(data)

    # ------------------------------------------------------------------
    # 入口与回退链
    # ------------------------------------------------------------------

    @handle("twitter.com", r"twitter\.com/[0-9a-zA-Z_]{1,20}/status/(?P<tid>[0-9]+)")
    @handle("x.com", r"x\.com/[0-9a-zA-Z_]{1,20}/status/(?P<tid>[0-9]+)")
    async def _parse(self, searched: Match[str]) -> ParseResult:
        return await self.parse_tweet(searched.group("tid"))

    def _get_sources(self) -> list[Callable[[str], Any]]:
        """解析源, 按顺序尝试: X 直连失败时回退 fxtwitter -> vxtwitter"""
        return [self._parse_by_primary, self._parse_by_fallback, self._parse_by_vxtwitter]

    @staticmethod
    async def _retry_transient(attempt: Callable[[], Any]) -> Any:
        """瞬时网络错误(连接被重置/超时)重试 + 统一转成 ParseException

        网络层异常必须转成 ParseException, 否则会穿透 parse_tweet 的回退逻辑,
        直接冒泡成 matcher 失败 (表现为"发失败但再发一次就好了")。
        """
        for remaining in range(TRANSIENT_RETRY, -1, -1):
            try:
                return await attempt()
            except TRANSIENT_ERRORS as e:
                if remaining == 0:
                    raise ParseException(f"网络错误: {type(e).__name__}") from e
                logger.debug(f"X 请求瞬时失败({type(e).__name__}), {TRANSIENT_RETRY_DELAY}s 后重试")
                await sleep(TRANSIENT_RETRY_DELAY)
            except HTTPError as e:
                raise ParseException(f"请求失败: {type(e).__name__}") from e
            except ParseException:
                raise
            except Exception as e:
                # 响应解码失败 / 推文结构异常(tombstone 等)同样穿透会跳过后续回退源,
                # 统一转成 ParseException (CancelledError 是 BaseException, 不受影响)
                raise ParseException(f"解析异常: {str(e) or type(e).__name__}") from e

    async def parse_tweet(self, tweet_id: str) -> ParseResult:
        """按推文 id 解析: 主接口失败时自动回退到备用接口"""
        errors: list[str] = []
        for source in self._get_sources():
            try:
                return await self._retry_transient(lambda: source(tweet_id))
            except ParseException as e:
                errors.append(str(e) or type(e).__name__)

        detail = "; ".join(errors) if errors else "无可用解析源"
        raise ParseException(f"解析失败: {detail}")

    async def _parse_by_primary(self, tweet_id: str) -> ParseResult:
        """走 X 官方 GraphQL 接口"""
        async with self._client() as client:
            response = await client.get(
                TWEET_RESULT_API,
                params={
                    "variables": encode(
                        {
                            "tweetId": tweet_id,
                            "includePromotedContent": True,
                            "withBirdwatchNotes": True,
                            "withVoice": True,
                            "withCommunity": True,
                            "withV2Timeline": True,
                            "withQuickPromoteEligibilityTweetFields": True,
                        }
                    ).decode(),
                    "features": FEATURES.decode(),
                    "fieldToggles": FIELD_TOGGLES.decode(),
                },
                headers=await self._get_auth_headers(client),
            )

            if response.status_code >= 400:
                # 鉴权失效(401/403)时立刻弃用缓存 token, 下次解析重新激活,
                # 否则在 TTL 内会一直带着坏 token 撞兜底
                if response.status_code in (401, 403):
                    self.guest_token = None
                raise ParseException(f"主接口获取数据失败 {response.status_code}: {response.text[:200]}")

            try:
                payload = response.json()
            except Exception as e:
                raise ParseException("主接口返回了无效 JSON") from e
            if not isinstance(payload, dict):
                raise ParseException("主接口返回了无效 JSON 对象")

            tweet_result = (payload.get("data") or {}).get("tweetResult") or {}
            if not tweet_result:
                raise ParseException("主接口未返回 tweetResult")

            try:
                tweet = convert(tweet_result, TweetEntry)
            except Exception as e:
                logger.opt(exception=True).debug("解析 TweetResult 失败: {}", str(tweet_result)[:300])
                raise ParseException("解析推文结构失败") from e

            return await self._collect_result(tweet)
