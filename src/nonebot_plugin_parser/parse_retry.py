"""解析层即时重试（防平台偶发解析失败）。

与 L2 失败链接后台重试（failure_retry）是互补的两层机制：
- 本模块：用户消息上下文内同步重试，全部尝试失败才向用户报错，成功结果直接
  走正常发送链路 —— 应对抖音等平台结构改版/风控导致的快进快出型偶发失败
  （403 / 空 body / 风控拦截），几秒后的重试往往即可成功。
- L2（failure_retry）：失败入队后由定时任务隔几分钟重试，无消息上下文，成功
  也无法回推用户 —— 应对分钟级以上的风控窗口。

重试策略：
- 只重试普通异常（ParseException / 网络错误 / 未预期错误）；TipException /
  IgnoreException 是语义性结果，重试无意义，立即上抛。
- 超时（asyncio.TimeoutError）不重试：挂起型 parser 大概率重试同样挂起
  （见 curl_cffi 静默忽略 httpx.Timeout 的前科），且用户已等满整个超时预算。
- Telegram 平台豁免：其解析阶段含媒体同步下载（tdl 自身 timeout=600s 兜底），
  失败重试会整段重下，代价过高；与 parse_timeout 的豁免口径一致。
"""

import asyncio
from re import Match
from typing import TYPE_CHECKING

from nonebot import logger

from .config import pconfig
from .exception import TipException, ParseException, IgnoreException

if TYPE_CHECKING:
    from .parsers import BaseParser, ParseResult


async def parse_with_retry(parser: "BaseParser", keyword: str, searched: Match[str]) -> "ParseResult":
    """执行解析，失败后即时重试（次数 parse_retry_max，间隔指数退避）。

    最终失败时原样抛出最后一次异常，由调用方（parser_handler）走既有的失败
    链路（record_failure + fail 表情 + L2 后台重试），本模块不吞异常。
    """
    retries = pconfig.parse_retry_max
    base_delay = pconfig.parse_retry_delay
    timeout = pconfig.parse_timeout
    exempt = parser.platform.name == "telegram"

    for attempt in range(retries + 1):
        try:
            if timeout > 0 and not exempt:
                return await asyncio.wait_for(parser.parse(keyword, searched), timeout=timeout)
            return await parser.parse(keyword, searched)
        except (TipException, IgnoreException, asyncio.TimeoutError):
            # 语义性结果 / 顶层超时不重试，立即上抛
            raise
        except Exception as e:
            if exempt or attempt >= retries:
                # Telegram 豁免即时重试（重试=整段媒体重下）；重试次数用尽原样上抛
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                f"[{parser.platform.display_name}] 解析失败"
                f"(第 {attempt + 1}/{retries + 1} 次尝试): {e!r}, {delay:g}s 后重试"
            )
            await asyncio.sleep(delay)
    # 循环内必然 return 或 raise，此行仅为满足类型检查的穷尽性
    raise ParseException("unreachable")
