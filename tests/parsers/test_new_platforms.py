"""测试新增平台解析器：知乎、网易云、酷狗、汽水音乐、虎扑、酷安、
LOFTER、堆糖、BUFF、小黑盒、ILLU、贴吧。

均为真实网络请求，失败时 skip（与现有 parser 测试风格一致）。
"""

import pytest

ZHIHU_ANSWER = "https://www.zhihu.com/question/67423622/answer/1396759249"
# 酷狗「舍得 - 王唯旖」(免费歌曲 privilege=0)，hash 直接在 URL 参数中
KUGOU_SHARE = "https://t.kugou.com/song/?hash=62C406C76F45C3EF39F451F2C4F22D95"
# 酷狗分享链接（chain 格式，hash 在页面 body 的 JSON 里）
KUGOU_CHAIN = "https://m.kugou.com/share/song.html?chain=4lDUBfcG3V2"
# QQ 音乐「同桌的你」(天天) songmid，实测免费歌曲匿名可解析
QQMUSIC_SONG = "https://y.qq.com/n/ryqq/songDetail/002Qvhtb46OI7q"
HUPU = "https://bbs.hupu.com/639669147.html"
COOLAPK = "https://www.coolapk.com/feed/58619217"
LOFTER = "https://www.lofter.com/post/30e8bd_1c9e1a3a0"
DUITANG = "https://www.duitang.com/atlas/?id=12878945"
BUFF_NEWS = "https://buff.163.com/s/news-detail_share.html?article_id=87832&comment_type=211"
HEYBOX = "https://www.xiaoheihe.cn/app/bbs/link/abc123"
ILLU_DRAWING = "https://illund.com/share.html?al=mindlib%3A%2F%2Freactbox%2F%3Fmainid%3Dfcecc2da36"
TIEBA = "https://tieba.baidu.com/p/9502327656"


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


# QQ 音乐分享卡片的 playsong.html 格式 (i.y.qq.com 域, songmid 在查询参数)
# 关键词匹配需覆盖卡片场景: extract_plain_text 跳过 json 段, 故走 _extract_url
# 提取 jumpUrl, 该 URL 不含 songDetail/ 故现有长链正则不匹配。
QQMUSIC_CARD_URL = (
    "https://i.y.qq.com/v8/playsong.html?platform=11&appshare=android_qq"
    "&appversion=14090008&hosteuin=oK-P7i4lowoq7v**&songmid=003vP9J945Wv8J"
    "&type=0&appsongtype=1&_wv=1&source=qq&ADTAG=qfshare"
)


def test_qqmusic_card_playsong_url_matches():
    """离线: QQ音乐分享卡片的 playsong.html?songmid= 格式应能被 search_url 匹配。

    卡片 jumpUrl 是 i.y.qq.com/v8/playsong.html?songmid=xxx, 与长链 songDetail/ 不同,
    需独立的 @handle 覆盖。纯单元测试 (只测 search_url, 不触发网络请求)。
    """
    from nonebot_plugin_parser.parsers import QQMusicParser

    if QQMusicParser is None:
        pytest.skip("qqmusic-api-python 未安装, QQMusicParser 不可用")
    parser = QQMusicParser()
    _, matched = parser.search_url(QQMUSIC_CARD_URL)
    assert matched, "QQ音乐卡片 playsong.html URL 应被匹配"
    assert matched.group("song_id") == "003vP9J945Wv8J", "应从 songmid= 提取歌曲 id"


def test_qqmusic_long_url_not_regression_after_card_handler():
    """回归: 加 playsong.html 卡片 handler 后, 长链 songDetail 仍正确路由。

    两者 keyword 不同 (playsong.html vs y.qq.com), 卡片 URL 含 y.qq.com 但不含
    songDetail/, 不会误匹配长链正则; 长链不含 playsong.html, 不会误匹配卡片正则。
    """
    from nonebot_plugin_parser.parsers import QQMusicParser

    if QQMusicParser is None:
        pytest.skip("qqmusic-api-python 未安装, QQMusicParser 不可用")
    parser = QQMusicParser()
    # 长链 songDetail 仍正常匹配
    _, matched = parser.search_url(QQMUSIC_SONG)
    assert matched, "长链 songDetail URL 不应因卡片 handler 回归"
    assert matched.group("song_id") == "002Qvhtb46OI7q"
    # 卡片 URL 不会误匹配到长链 keyword (keyword != "y.qq.com")
    keyword_card, _ = parser.search_url(QQMUSIC_CARD_URL)
    assert keyword_card != "y.qq.com", "卡片 URL 应走 playsong.html handler 而非长链"


@pytest.mark.asyncio
async def test_qqmusic():
    from nonebot_plugin_parser.parsers import QQMusicParser
    from nonebot_plugin_parser.exception import IgnoreException

    parser = QQMusicParser()
    keyword, matched = parser.search_url(QQMUSIC_SONG)
    assert matched, "QQ音乐歌曲 URL 应被匹配"
    try:
        result = await parser.parse(keyword, matched)
    except IgnoreException as e:
        # 未登录态下 VIP/付费曲目拿不到播放地址属预期，跳过而非判失败
        pytest.skip(f"QQ音乐该曲目无匿名播放权限（VIP/付费），跳过: {e!r}")
    except Exception as e:
        pytest.skip(f"QQ音乐解析失败（网络/第三方服务），跳过: {e!r}")
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
async def test_kugou_chain():
    """酷狗分享链接 chain 格式：hash 在页面 body 的 JSON 里，需请求页面提取。"""
    from nonebot_plugin_parser.parsers import KuGouParser

    parser = KuGouParser()
    keyword, matched = parser.search_url(KUGOU_CHAIN)
    assert matched, "chain 分享链接应被匹配"
    # 正则应匹配完整 URL（含 ?chain= 参数），不被截断在 .html 处
    assert "chain=" in matched.group(0), "应包含 chain 参数"
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"酷狗解析失败，跳过: {e!r}")
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


@pytest.mark.asyncio
async def test_lofter():
    from nonebot_plugin_parser.parsers import LofterParser

    parser = LofterParser()
    keyword, matched = parser.search_url(LOFTER)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"LOFTER 解析失败，跳过: {e!r}")
    assert result.author, "应提取作者"


@pytest.mark.asyncio
async def test_duitang():
    from nonebot_plugin_parser.parsers import DuiTangParser

    parser = DuiTangParser()
    keyword, matched = parser.search_url(DUITANG)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"堆糖解析失败，跳过: {e!r}")
    assert result.graphics, "应有图集内容"


@pytest.mark.asyncio
async def test_buff():
    from nonebot_plugin_parser.parsers import BuffParser

    parser = BuffParser()
    keyword, matched = parser.search_url(BUFF_NEWS)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"BUFF 解析失败，跳过: {e!r}")
    assert result.title, "应提取标题"


@pytest.mark.asyncio
async def test_heybox():
    from nonebot_plugin_parser.parsers import HeyBoxParser

    parser = HeyBoxParser()
    keyword, matched = parser.search_url(HEYBOX)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"小黑盒解析失败，跳过: {e!r}")
    assert result.title, "应提取标题"


@pytest.mark.asyncio
async def test_illu():
    from nonebot_plugin_parser.parsers import IlluParser

    parser = IlluParser()
    keyword, matched = parser.search_url(ILLU_DRAWING)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"ILLU 解析失败，跳过: {e!r}")
    assert result.title, "应提取标题"


@pytest.mark.asyncio
async def test_tieba():
    from nonebot_plugin_parser.parsers import TiebaParser

    parser = TiebaParser()
    keyword, matched = parser.search_url(TIEBA)
    assert matched
    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"贴吧解析失败，跳过: {e!r}")
    assert result.title, "应提取标题"
    assert result.author, "应提取作者"
