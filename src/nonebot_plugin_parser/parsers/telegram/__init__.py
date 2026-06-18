"""Telegram 解析器（基于 tdl 二进制）。

通过本机 tdl CLI 下载 t.me 链接对应的媒体，并提取元数据（文件名/文案/时间）。
频道帖子的发送者即频道本身，故 author 使用频道用户名。

需权限：仅 SUPERUSER 或被 SUPERUSER 授权（`tg授权`）的用户可用。
权限网关在 matchers/__init__.py 的 parser_handler 中实现。
"""

import re
from typing import ClassVar

from nonebot import logger

from ..base import (
    Platform,
    BaseParser,
    PlatformEnum,
    ParseException,
    handle,
    pconfig,
)
from ...download import (
    download_media,
    fetch_messages,
    classify_by_ext,
    is_tdl_available,
)


class TelegramParser(BaseParser):
    """Telegram 链接解析器"""

    platform: ClassVar[Platform] = Platform(name=PlatformEnum.TELEGRAM, display_name="TG")

    def __init__(self):
        super().__init__()

    # https://t.me/anyul996/28
    # https://t.me/anyul996/28/42 (话题消息：channel/topic_id/msg_id)
    # 排除 t.me/share/url 等功能性链接；频道用户名至少 5 位
    @handle("t.me", r"https?://t\.me/(?!share/url)(?P<channel>[A-Za-z0-9_]{5,})/(?:\d+/)?(?P<msgid>\d+)")
    async def _parse(self, searched: re.Match[str]):
        if not is_tdl_available():
            raise ParseException("tdl 不可用，请联系管理员安装 tdl 并执行 `tdl login`，或配置 parser_tdl_path")

        channel = searched.group("channel")
        message_id = int(searched.group("msgid"))
        url = searched.group(0)
        # 规范化为完整 URL
        if not url.startswith("http"):
            url = f"https://{url}"

        logger.info(f"[Telegram] 解析 {url} (channel={channel}, msg={message_id})")

        # 1. 获取元数据
        try:
            msg = await fetch_messages(channel, message_id)
        except ParseException:
            raise
        except Exception as e:
            raise ParseException(f"获取 Telegram 消息元数据失败: {e}") from e

        filename = msg.get("file") or ""
        caption = msg.get("text") or msg.get("message") or ""
        date = msg.get("date")

        if not filename:
            # 无文件（纯文本消息）：仅返回文案
            return self.result(
                title=None,
                text=caption or "(无媒体、无文案)",
                author=self.create_author(name=f"@{channel}"),
                timestamp=date,
                url=url,
            )

        # 2. 下载媒体到 cache_dir
        # 用 url 哈希作为文件名前缀，便于去重
        from ...utils import generate_file_name

        target_base = generate_file_name(url)  # 不带扩展名
        try:
            paths = await download_media(
                url,
                dest_dir=pconfig.cache_dir,
                target_filename=target_base,
            )
        except ParseException:
            raise
        except Exception as e:
            raise ParseException(f"下载 Telegram 媒体失败: {e}") from e

        if not paths:
            raise ParseException("Telegram 媒体下载完成但未获得文件")

        # 3. 按扩展名分类，构造内容
        contents = []
        for path in paths:
            media_type = classify_by_ext(path.name)
            match media_type:
                case "video":
                    # path 直接传入（已是 Path，非下载任务）
                    contents.append(self.create_video_content(path))
                case "audio":
                    contents.append(self.create_audio_content(path))
                case "image":
                    contents.append(self.create_image_content(path, alt=filename))
                case _:
                    # 其他文件类型：作为图片内容降级（发送时会变成图片失败，
                    # 但 render 会处理；更好的做法是作为文件发送，目前统一走 image）
                    logger.warning(f"[Telegram] 未知文件类型: {path.name}")
                    contents.append(self.create_image_content(path, alt=filename))

        # 4. 元数据
        title = filename if len(paths) == 1 else f"{filename} 等 {len(paths)} 个文件"
        author = self.create_author(name=f"@{channel}")

        return self.result(
            title=title,
            text=caption or None,
            author=author,
            contents=contents,
            timestamp=date,
            url=url,
        )
