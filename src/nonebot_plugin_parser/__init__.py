import asyncio

from nonebot import logger, require, get_driver
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")

from .utils import safe_unlink
from .config import Config, pconfig
from .download import DOWNLOADER
from .matchers import clear_result_cache

__plugin_meta__ = PluginMetadata(
    name="链接分享解析 Alconna 版",
    description="支持B站|抖音|快手|微博|小红书|YouTube|TikTok|Twitter|AcFun|NGA",
    usage=(
        "发送支持平台的(BV号/链接/小程序/卡片)即可\n"
        "其他命令:\n"
        "  bm BV号 <分集> (下载B站音频)\n"
        "  ym 链接 (下载油管音频)\n"
        "  blogin (扫码获取B站凭据)"
    ),
    type="application",
    homepage="https://github.com/fllesser/nonebot-plugin-parser",
    config=Config,
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna", "nonebot_plugin_uninfo"),
    extra={
        "author": "fllesser",
        "email": "fllessive@gmail.com",
        "homepage": "https://github.com/fllesser/nonebot-plugin-parser",
    },
)

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .download import is_tdl_available


@get_driver().on_startup
def _check_tdl():
    """启动时检测 tdl 二进制是否可用，不可用则降级（仅 Telegram 解析受影响）。"""
    if is_tdl_available():
        logger.info("tdl 可用，Telegram 解析已就绪")
    else:
        logger.warning(
            "tdl 二进制不可用，Telegram 解析将不可用。"
            "请安装 tdl (https://github.com/iyear/tdl) 并执行 `tdl login`，"
            "或配置 parser_tdl_path 指向 tdl 路径。"
        )


@get_driver().on_startup
def _check_qqmusic():
    """启动时检测 qqmusic-api-python 是否可用，缺包则降级提醒（仅 QQ 音乐解析受影响）。

    qqmusic-api-python 是主依赖，但环境异常（装包失败/被卸载）时缺包不应阻塞整个
    插件启动。这里仅 warning 提醒，其余平台照常工作。
    """
    from .parsers import _QQMUSIC_AVAILABLE

    if _QQMUSIC_AVAILABLE:
        return
    logger.warning(
        "qqmusic-api-python 未安装或加载失败，QQ音乐解析将不可用（其余平台正常）。"
        "执行 `pip install qqmusic-api-python` 或 `uv sync` 后重启即可恢复。"
    )


@get_driver().on_shutdown
async def close_downloader():
    await DOWNLOADER.close()


@scheduler.scheduled_job("cron", hour=1, minute=0, id="parser-clean-local-cache")
async def clean_plugin_cache():
    try:
        files = [f for f in pconfig.cache_dir.iterdir() if f.is_file()]
        if not files:
            logger.info("No cache files to clean")
            return

        # 并发删除文件
        tasks = [safe_unlink(file) for file in files]
        await asyncio.gather(*tasks)

        logger.success(f"Successfully cleaned {len(files)} cache files")
    except Exception:
        logger.exception("Error while cleaning cache files")

    # 资源清理完毕后，清理 result 缓存
    clear_result_cache()


def _setup_failure_retry_job() -> None:
    """注册失败链接定时重试 job（L2）。

    间隔分钟数由 pconfig.failure_retry_interval 决定（默认 10 分钟）。
    配置关闭时不注册。单独函数以便动态读配置。
    """
    if not pconfig.failure_retry_enabled:
        return
    interval = pconfig.failure_retry_interval

    @scheduler.scheduled_job("interval", minutes=interval, id="parser-failure-retry")
    async def _failure_retry_job():
        from .failure_retry import run_failure_retry

        try:
            await run_failure_retry()
        except Exception:
            logger.exception("失败链接重试 job 异常")


_setup_failure_retry_job()
