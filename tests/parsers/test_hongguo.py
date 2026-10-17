"""红果短剧 parser 真实网络测试（tests/parsers/ 自动带 network marker）。"""

import asyncio

import httpx
import pytest
from nonebot import logger


def _cancel_pending(result) -> None:
    """解析返回的延迟下载任务在断言结束后取消，避免 un-awaited 告警。"""
    for content in result.contents:
        task = getattr(content, "path_task", None)
        if asyncio.isfuture(task) and not task.done():
            task.cancel()


def _skip_if_upstream_dead(e: Exception) -> None:
    """红果上游播放保护收紧 (2026-10-17 实测) 的定向 skip。

    App video_model 端点一律 200+空 body (换设备/换版本号均无效), web player 页
    SSR 只下发 protectedPlayback=true + publicVideoSeo 不再内嵌播放数据, 两渠道
    全灭属上游变更而非解析回归, 按 kuaishou 空响应先例转 skip (上游恢复或重新
    逆向接入后自动恢复实跑)。其余异常原样上抛, 不掩盖解析层回归。
    """
    text = str(e)
    if "红果播放接口无有效响应" in text or "红果网页播放数据为空" in text:
        pytest.skip(f"红果上游播放保护收紧, 取流渠道全灭 (待重新逆向): {text}")


@pytest.mark.asyncio
async def test_share_short_link():
    """App 分享短链（真人剧，schemeParams 带 series_id + vid）。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    url = "https://novelquickapp.com/s/FO_dOvfhq54/"
    keyword, searched = parser.search_url(url)
    assert searched

    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")
    except Exception as e:
        _skip_if_upstream_dead(e)
        raise

    try:
        logger.info(f"解析结果: {result}")
        assert result.title, "标题为空"
        assert result.video_contents, "应包含视频内容"
        assert result.url == url
    finally:
        _cancel_pending(result)


@pytest.mark.asyncio
async def test_web_detail_link():
    """网页版 detail 链接（默认取第 1 集）。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    url = "https://hongguoduanju.com/detail?series_id=7689336045552274457"
    keyword, searched = parser.search_url(url)

    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")
    except Exception as e:
        _skip_if_upstream_dead(e)
        raise

    try:
        logger.info(f"解析结果: {result}")
        assert "人在诡异" in (result.title or ""), f"标题异常: {result.title}"
        assert result.video_contents, "应包含视频内容"
    finally:
        _cancel_pending(result)


@pytest.mark.asyncio
async def test_share_text_only():
    """纯文本分享（无链接）→ 站内搜索 → 第 1 集。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    text = "漫剧《人在诡异，靠第四天灾登顶至高》 - 免费好剧，尽在红果"
    keyword, searched = parser.search_url(text)

    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")
    except Exception as e:
        _skip_if_upstream_dead(e)
        raise

    try:
        assert result.title, "标题为空"
        assert result.video_contents, "应包含视频内容"
    finally:
        _cancel_pending(result)


@pytest.mark.asyncio
async def test_full_episode_via_app_api():
    """非游客可看分集（第 51 集）经 App 接口 + CENC 解密拿到可播放 mp4。

    原则「无 skip 降级」保持: 解密链路是本平台核心路径, 除上游失效外的一切
    异常照常 raise。2026-10-17 起上游播放保护收紧致取流全灭 (见
    _skip_if_upstream_dead), 该形态下解密链路根本不会被触达, 定向 skip 的
    只是这种整体失效; 上游恢复后自动恢复实跑。
    """
    from nonebot_plugin_parser.utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg 不可用，跳过解密链路验证")

    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    url = "https://hongguoduanju.com/player/7689336045552274457/7689337825312918553"
    keyword, searched = parser.search_url(url)
    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")
    except Exception as e:
        _skip_if_upstream_dead(e)
        raise
    try:
        assert result.video_contents
        path = await result.video_contents[0].get_path()
        assert path.exists()
        assert path.stat().st_size > 0
    finally:
        _cancel_pending(result)
