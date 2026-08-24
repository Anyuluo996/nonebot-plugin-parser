import os
import asyncio

from nonebot import logger, require, get_driver
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

# htmlrender 0.8 起渲染配置迁入 `render` 命名空间且默认不选 provider（无位图
# 渲染能力）、默认拒绝一切本地路径读取（模板/字体加载直接失败）。parser 的
# 渲染栈固定为 playwright（[htmlrender] extra 即 nonebot-plugin-htmlrender
# [playwright]），模板在插件包内、字体/缓存路径用户可任意指定，无法用
# allowed_paths 枚举。require htmlrender 前注入默认值保持 0.7 行为；
# setdefault 不覆盖用户显式配置（如切换 takumi provider 的部署）。
os.environ.setdefault("RENDER__PROVIDER", "playwright")
os.environ.setdefault("RENDER__RESOURCES__LOCAL_ACCESS__ALLOW_ANY_PATH", "true")

# htmlrender 0.8 的 playwright provider 经 localstore 取存储目录，localstore 按
# 调用栈解析"caller 插件"——htmlrender 必须通过 require 注册为 nonebot 插件，
# 直接 from nonebot_plugin_htmlrender import 只执行模块级 bootstrap 不注册，
# 运行时会抛 "Cannot detect caller plugin"。渲染器模块都是延迟 import 的，
# 其模块级 require 不可靠，这里在插件加载时统一 require 一次。
try:
    require("nonebot_plugin_htmlrender")
except Exception:  # 未安装 htmlrender extra 时走 htmlkit 回退，不阻塞插件加载
    logger.debug("nonebot_plugin_htmlrender 未安装，渲染将回退 htmlkit")

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


@scheduler.scheduled_job("interval", minutes=5, id="parser-tg-2fa-cleanup")
async def _cleanup_tg_2fa_pending():
    """周期清理 ``_tg_2fa_pending`` 中陈旧的 2FA handle。

    用户触发 2FA 后若不发密码(放弃/掉线/超时), 对应的 ``LoginQrHandle`` 会一直
    持有 pty fd、tdl 子进程、daemon 读线程, 造成资源泄漏。本 job 每 5 分钟扫描一次,
    清理创建超过 10 分钟仍未被消费的 pending handle (调用 tdl._terminate_handle
    kill 进程组 + 关 fd)。正常路径(用户发密码消费)不受影响。
    """
    from .matchers.tg_login import _cleanup_stale_2fa

    try:
        n = _cleanup_stale_2fa(max_age_seconds=600)
        if n > 0:
            logger.info(f"清理 {n} 个陈旧的 Telegram 2FA pending handle")
    except Exception:
        logger.exception("tg 2fa pending 清理 job 异常")
