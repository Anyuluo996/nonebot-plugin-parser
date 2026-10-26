"""测试新增平台解析器：知乎、网易云、酷狗、汽水音乐、虎扑、酷安、
LOFTER、堆糖、BUFF、小黑盒、ILLU、贴吧。

均为真实网络请求。**只有「上游不可用」类异常才 skip**（链接失效/下架/风控/无
播放权限/网络不通/环境缺依赖——这些重跑也一样，改代码也变不绿）；解析器自身的
编程错误（TypeError/KeyError/IndexError/AttributeError/msgspec 校验错误等）必须
上抛让用例失败，否则平台改版造成的结构性回归会被 skip 掩盖，CI 永远绿。
分类依据见 ``_is_upstream_unavailable`` 注释。
"""

import contextlib

import httpx
import pytest

# 上游明确拒绝时的文案特征（删除/下架/风控/付费墙/限流）。
# 同时适用于 ParseException 与 ValueError: 贴吧 utils 直接把接口 errmsg 包成 ValueError。
_UPSTREAM_PARSE_HINTS = (
    "删除",
    "不存在",
    "失效",
    "风控",
    "验证码",
    "频繁",
    "限流",
    "下架",
    "无版权",
    "版权",
    "VIP",
    "vip",
    "付费",
    "未找到",
    "没有可播放",
)


def _is_upstream_unavailable(error: BaseException) -> bool:
    """判断异常是否来自「上游不可用」而非解析器自身缺陷。

    skip 白名单（按异常来源分类）:
    - ``httpx.TransportError`` 家族: 连接/超时/代理/TLS 等网络层失败；
    - ``httpx.HTTPStatusError``: 上游 4xx/5xx（403 风控、404 删除、400 参数失效等）；
    - ``curl_cffi`` 的请求异常（酷狗/NGA 走 TLS 指纹链路时的网络失败）；
    - ``IgnoreException`` / ``TipException``: 业务上「忽略/提示用户」的语义化分支
      （内容不可见、无播放权限等），不是代码缺陷；
    - 消息带删除/失效/风控/VIP 等语义的 ``ParseException`` / ``ValueError``:
      上游明确拒绝该内容（ValueError 覆盖贴吧把接口 errmsg 直接上抛的写法）；
    - 环境缺失: ``ModuleNotFoundError`` 提到 nonebot、nonebot 的
      ``Cannot load plugin`` / ``Cannot detect caller plugin``（caller-plugin 未加载）。

    不在白名单里的（TypeError/KeyError/IndexError/AttributeError/msgspec 校验错误/
    其余 ValueError 等）视为解析器崩溃，调用方必须让用例失败。
    """
    # 网络层（httpx）: TransportError 含 TimeoutException/ConnectError/ProxyError 等子类
    if isinstance(error, (httpx.HTTPStatusError, httpx.TransportError)):
        return True

    # 酷狗/NGA 等用 curl_cffi: 网络失败抛其自有异常，按模块名判定以免硬依赖该包
    if type(error).__module__.startswith("curl_cffi"):
        return True

    # 环境缺依赖/缺 caller-plugin（与平台上游无关，也不是解析器缺陷）
    if isinstance(error, ModuleNotFoundError) and "nonebot" in str(error):
        return True
    if isinstance(error, RuntimeError) and any(
        hint in str(error) for hint in ("Cannot load plugin", "Cannot detect caller plugin")
    ):
        return True

    from nonebot_plugin_parser.exception import TipException, ParseException, IgnoreException

    # 语义化业务异常: 明确表示「无法解析该内容」而非代码崩
    if isinstance(error, (IgnoreException, TipException)):
        return True
    if isinstance(error, (ParseException, ValueError)):
        message = str(error)
        return any(hint in message for hint in _UPSTREAM_PARSE_HINTS)

    return False


@contextlib.contextmanager
def _skip_on_upstream_unavailable(platform: str):
    """解析阶段守卫: 上游不可用才 skip，其余异常原样上抛让用例失败。"""
    try:
        yield
    except Exception as e:
        if not _is_upstream_unavailable(e):
            raise
        pytest.skip(f"{platform} 上游不可用（网络/风控/链接失效），跳过: {e!r}")


ZHIHU_ANSWER = "https://www.zhihu.com/question/67423622/answer/1396759249"
# 酷狗「舍得 - 王唯旖」(免费歌曲 privilege=0)，hash 直接在 URL 参数中
# 注: 该分享链接已失效 (404)，test_kugou 会 skip；需替换为新的免费歌曲 hash
KUGOU_SHARE = "https://t.kugou.com/song/?hash=62C406C76F45C3EF39F451F2C4F22D95"
# 酷狗分享链接（chain 格式，hash 在页面 body 的 JSON 里）
KUGOU_CHAIN = "https://m.kugou.com/share/song.html?chain=4lDUBfcG3V2"
# QQ 音乐「同桌的你」(天天) songmid，实测免费歌曲匿名可解析
QQMUSIC_SONG = "https://y.qq.com/n/ryqq/songDetail/002Qvhtb46OI7q"
HUPU = "https://bbs.hupu.com/639669147.html"
COOLAPK = "https://www.coolapk.com/feed/58619217"
# 注: 该 LOFTER 帖子已删除 (404)，test_lofter 会 skip
LOFTER = "https://www.lofter.com/post/30e8bd_1c9e1a3a0"
DUITANG = "https://www.duitang.com/atlas/?id=12878945"
BUFF_NEWS = "https://buff.163.com/s/news-detail_share.html?article_id=87832&comment_type=211"
HEYBOX = "https://www.xiaoheihe.cn/app/bbs/link/abc123"
# 小黑盒 QQ 分享卡片 jumpUrl：api.xiaoheihe.cn/v3/bbs/app/api/web/share?...&link_id=xxx
# keyword 子串门要求 keyword 是 URL 子串，否则 regex 不参与匹配（见 rule.py KeywordRegexRule）
HEYBOX_SHARE = (
    "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share"
    "?h_camp=link&h_session_id=AH5mHiiMSRm6yiTt&h_src=YXBwX3NoYXJl&link_id=9a05724436e0"
)
ILLU_DRAWING = "https://illund.com/share.html?al=mindlib%3A%2F%2Freactbox%2F%3Fmainid%3Dfcecc2da36"
TIEBA = "https://tieba.baidu.com/p/9502327656"


@pytest.mark.asyncio
async def test_zhihu_answer():
    from nonebot_plugin_parser.parsers import ZhiHuParser

    parser = ZhiHuParser()
    keyword, matched = parser.search_url(ZHIHU_ANSWER)
    assert matched, "知乎回答 URL 应被匹配"
    with _skip_on_upstream_unavailable("知乎"):
        result = await parser.parse(keyword, matched)
    assert result.title, "应提取标题"
    assert result.author, "应提取作者"
    assert result.graphics, "应有正文富内容"


@pytest.mark.asyncio
async def test_netease():
    from nonebot_plugin_parser.parsers import NCMParser

    parser = NCMParser()
    # 注: 歌曲 id=1945263 已下架/无版权，test_netease 会 skip；需替换为有效的免费歌曲 id
    keyword, matched = parser.search_url("https://y.music.163.com/m/song?id=1945263")
    assert matched
    with _skip_on_upstream_unavailable("网易云"):
        result = await parser.parse(keyword, matched)
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

    parser = QQMusicParser()
    keyword, matched = parser.search_url(QQMUSIC_SONG)
    assert matched, "QQ音乐歌曲 URL 应被匹配"
    # 未登录态下 VIP/付费曲目拿不到播放地址属上游限制（IgnoreException），由守卫 skip
    with _skip_on_upstream_unavailable("QQ音乐"):
        result = await parser.parse(keyword, matched)
    assert result.contents, "应有音频内容"


@pytest.mark.asyncio
async def test_kugou():
    from nonebot_plugin_parser.parsers import KuGouParser

    parser = KuGouParser()
    keyword, matched = parser.search_url(KUGOU_SHARE)
    assert matched
    with _skip_on_upstream_unavailable("酷狗"):
        result = await parser.parse(keyword, matched)
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
    with _skip_on_upstream_unavailable("酷狗"):
        result = await parser.parse(keyword, matched)
    assert result.contents, "应有音频内容"


@pytest.mark.asyncio
async def test_hupu():
    from nonebot_plugin_parser.parsers import HupuParser

    parser = HupuParser()
    keyword, matched = parser.search_url(HUPU)
    assert matched
    with _skip_on_upstream_unavailable("虎扑"):
        result = await parser.parse(keyword, matched)
    assert result.title, "应提取标题"
    assert result.author, "应提取作者"


@pytest.mark.asyncio
async def test_coolapk():
    from nonebot_plugin_parser.parsers import CoolapkParser

    parser = CoolapkParser()
    keyword, matched = parser.search_url(COOLAPK)
    assert matched
    with _skip_on_upstream_unavailable("酷安"):
        result = await parser.parse(keyword, matched)
    assert result.author, "应提取作者"


@pytest.mark.asyncio
async def test_lofter():
    from nonebot_plugin_parser.parsers import LofterParser

    parser = LofterParser()
    keyword, matched = parser.search_url(LOFTER)
    assert matched
    with _skip_on_upstream_unavailable("LOFTER"):
        result = await parser.parse(keyword, matched)
    assert result.author, "应提取作者"


@pytest.mark.asyncio
async def test_duitang():
    from nonebot_plugin_parser.parsers import DuiTangParser

    parser = DuiTangParser()
    keyword, matched = parser.search_url(DUITANG)
    assert matched
    with _skip_on_upstream_unavailable("堆糖"):
        result = await parser.parse(keyword, matched)
    assert result.graphics, "应有图集内容"


@pytest.mark.asyncio
async def test_buff():
    from nonebot_plugin_parser.parsers import BuffParser

    parser = BuffParser()
    keyword, matched = parser.search_url(BUFF_NEWS)
    assert matched
    with _skip_on_upstream_unavailable("BUFF"):
        result = await parser.parse(keyword, matched)
    assert result.title, "应提取标题"


@pytest.mark.asyncio
async def test_heybox():
    from nonebot_plugin_parser.parsers import HeyBoxParser

    parser = HeyBoxParser()
    keyword, matched = parser.search_url(HEYBOX)
    assert matched
    with _skip_on_upstream_unavailable("小黑盒"):
        result = await parser.parse(keyword, matched)
    assert result.title, "应提取标题"


def test_heybox_share_card_url_matches():
    """离线: 小黑盒 QQ 分享卡片 jumpUrl 应能被 search_url 匹配。

    卡片 jumpUrl 是 api.xiaoheihe.cn/v3/bbs/app/api/web/share?...&link_id=xxx,
    与标准 app/bbs/link/{id} 格式不同。原 keyword (xiaoheihe.cn/app/bbs、
    xiaoheihe.cn/bbs/post_share) 都不是该 URL 子串, keyword 子串门 (rule.py
    KeywordRegexRule) 在 regex 之前就跳过, 导致 link_id 无法提取、静默不解析。
    纯单元测试 (只测 search_url, 不触发网络请求)。
    """
    from nonebot_plugin_parser.parsers import HeyBoxParser

    parser = HeyBoxParser()
    _, matched = parser.search_url(HEYBOX_SHARE)
    assert matched, "小黑盒分享卡片 web/share URL 应被匹配"
    assert matched.group("link_id") == "9a05724436e0", "应从 link_id= 提取帖子 id"


def test_heybox_long_url_not_regression_after_share_handler():
    """回归: 加 web/share 卡片 handler 后, 标准 app/bbs/link 长链仍正确路由。

    三者 keyword 互斥: web/share 路径不含 app/bbs 或 bbs/post_share 子串,
    app/bbs/link 路径不含 web/share 子串, 不会互相误匹配。
    """
    from nonebot_plugin_parser.parsers import HeyBoxParser

    parser = HeyBoxParser()
    # 标准长链仍正常匹配
    _, matched = parser.search_url(HEYBOX)
    assert matched, "标准 app/bbs/link URL 不应因 share handler 回归"
    assert matched.group("link_id") == "abc123"
    # 卡片 URL 走 web/share handler 而非 app/bbs handler
    keyword_card, _ = parser.search_url(HEYBOX_SHARE)
    assert keyword_card == "xiaoheihe.cn/v3/bbs/app/api/web/share", "卡片 URL 应走 web/share handler"


@pytest.mark.asyncio
async def test_illu():
    from nonebot_plugin_parser.parsers import IlluParser

    parser = IlluParser()
    keyword, matched = parser.search_url(ILLU_DRAWING)
    assert matched
    with _skip_on_upstream_unavailable("ILLU"):
        result = await parser.parse(keyword, matched)
    assert result.title, "应提取标题"


@pytest.mark.asyncio
async def test_tieba():
    from nonebot_plugin_parser.parsers import TiebaParser

    parser = TiebaParser()
    keyword, matched = parser.search_url(TIEBA)
    assert matched
    with _skip_on_upstream_unavailable("贴吧"):
        result = await parser.parse(keyword, matched)
    assert result.title, "应提取标题"
    assert result.author, "应提取作者"
