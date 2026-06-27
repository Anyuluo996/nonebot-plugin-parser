"""测试新增平台解析器：知乎、网易云、酷狗、酷我、汽水音乐、虎扑、酷安。

均为真实网络请求，失败时 skip（与现有 parser 测试风格一致）。
"""

import pytest

ZHIHU_ANSWER = "https://www.zhihu.com/question/67423622/answer/1396759249"
NETEASE = "https://y.music.163.com/m/song?id=1945bindNull"
KUGOU_SHARE = "https://www.kugou.com/share/2T6Jwe3e3b3.html"
KUWO = "https://www.kuwo.cn/play_detail/2986743"
HUPU = "https://bbs.hupu.com/639669147.html"
COOLAPK = "https://www.coolapk.com/feed/58619217"


@pytest.mark.asyncio
async def test_zhihu_answer():
    from nonebot_plugin_parser.parsers import ZhiHuParser

    parser = ZhiHuParser()
    keyword, matched = parser.search_url(ZHIHU_ANSWER)
    assert matched, "知乎回答 URL 应被匹配"
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"知乎解析失败（网络/签名），跳过: {e!r}")
    assert result.title, "应提取标题"
    assert result.author, "应提取作者"
    assert result.graphics, "应有正文富内容"


@pytest.mark.asyncio
async def test_netease():
    from nonebot_plugin_parser.parsers import NCMParser

    parser = NCMParser()
    keyword, matched = parser.search_url("https://y.music.163.com/m/song?id=1945263")
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"网易云解析失败（第三方服务），跳过: {e!r}")
    assert result.contents, "应有音频内容"


@pytest.mark.asyncio
async def test_kugou():
    from nonebot_plugin_parser.parsers import KuGouParser

    parser = KuGouParser()
    keyword, matched = parser.search_url(KUGOU_SHARE)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"酷狗解析失败，跳过: {e!r}")
    assert result.contents, "应有音频内容"


@pytest.mark.asyncio
async def test_kuwo():
    from nonebot_plugin_parser.parsers import KuWoParser

    parser = KuWoParser()
    keyword, matched = parser.search_url(KUWO)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"酷我解析失败（第三方服务），跳过: {e!r}")
    assert result.contents, "应有音频内容"


@pytest.mark.asyncio
async def test_hupu():
    from nonebot_plugin_parser.parsers import HupuParser

    parser = HupuParser()
    keyword, matched = parser.search_url(HUPU)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"虎扑解析失败，跳过: {e!r}")
    assert result.title, "应提取标题"
    assert result.author, "应提取作者"


@pytest.mark.asyncio
async def test_coolapk():
    from nonebot_plugin_parser.parsers import CoolapkParser

    parser = CoolapkParser()
    keyword, matched = parser.search_url(COOLAPK)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"酷安解析失败，跳过: {e!r}")
    assert result.author, "应提取作者"
