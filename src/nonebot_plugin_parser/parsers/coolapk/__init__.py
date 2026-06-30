"""酷安解析器。

适配自 parser-lite 的 coolapk。解析酷安动态 feed。
正文 message 为 HTML，picArr 为图片列表。
"""

import re
from typing import ClassVar

from bs4 import BeautifulSoup

from .feed import decoder as FeedDecoder
from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle

_NEXT_DATA = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*(.*?)\s*</script>',
    re.IGNORECASE | re.DOTALL,
)


class CoolapkParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.COOLAPK, display_name="酷安")

    @handle("coolapk1s.com/feed/", r"coolapk1s\.com/feed/(?P<feed_id>\d+)")
    @handle("www.coolapk.com/feed/", r"www\.coolapk\.com/feed/(?P<feed_id>\d+)")
    @handle("coolapk.com/feed/", r"coolapk\.com/feed/(?P<feed_id>\d+)")
    async def _parse(self, searched: re.Match[str]):
        feed_id = searched.group("feed_id")
        response = await self.request(f"https://www.coolapk1s.com/feed/{feed_id}")
        response.raise_for_status()

        if matched := _NEXT_DATA.search(response.text):
            next_data = FeedDecoder.decode(matched[1])
        else:
            raise ParseException(f"未找到酷安页面数据: {feed_id}")
        feed = next_data.props.pageProps

        text = BeautifulSoup(feed.feed.message, "html.parser").get_text(strip=True)
        graphics: list = []
        if text:
            graphics.append(text)
        for pic in feed.feed.picArr or []:
            graphics.append(self.create_image_content(pic))

        return self.result(
            title=feed.feed.title,
            graphics=graphics,
            timestamp=feed.feed.dateline,
            url=f"https://www.coolapk.com/feed/{feed_id}",
            author=self.create_author(
                name=feed.feed.username,
                avatar_url=feed.feed.userAvatar,
            ),
            extra={
                "ai_summary": feed.aiSummary,
            },
        )
