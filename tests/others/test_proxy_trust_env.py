"""代理策略单元测试：国内平台直连、海外平台显式代理。

回归本次故障（B 站短链解析被容器层 ``HTTP_PROXY`` 环境变量错误地经代理，
见 nb2 容器日志 ``httpcore.ConnectError: All connection attempts failed``）。

验证思路：monkeypatch ``httpx.AsyncClient``，捕获其构造参数，断言：
- 国内平台 / 短链重定向传入 ``trust_env=False``（不读环境变量代理）
- 海外平台（YouTube）显式传入 ``proxy=pconfig.proxy``
- ``request()`` 默认 ``trust_env=False``，但传 ``proxy=`` 时仍走代理
- ``request(trust_env=True)`` 保留逃生口

为什么不用 respx mock？respx 在 httpx transport 层短路，会在代理生效前返回
mock 响应，无法验证 trust_env 的真实行为。捕获构造参数是最直接的契约验证。

相关改动：
- base.py: request() 默认 trust_env=False；get_redirect_url/get_final_url 加 trust_env=False
- youtube: _fetch_author_info 显式 proxy=pconfig.proxy
- kugou_api: curl_cffi 显式传 proxies（国内空，有配置才走）
- weibo/xiaohongshu: 删除冗余 trust_env=False（已成默认）
"""

import httpx
import pytest

# 一个肯定不可达的代理：用于验证请求是否真的尝试走代理（会 ConnectError）。
_DEAD_PROXY = "http://10.255.255.1:1"


def _patch_asyncclient(monkeypatch, capture: dict):
    """替换 httpx.AsyncClient，捕获构造参数，模拟一个总是 200 的假响应。

    所有发往真实网络的请求都会被这个假 client 拦截，不走任何代理/网络。
    关键：捕获 ``trust_env`` 和 ``proxy`` 构造参数用于断言。
    """

    class _FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = httpx.Headers({"Location": "https://www.bilibili.com/video/BV1xx"})
            self._json = {"ok": True}

        @property
        def content(self):
            return b'{"ok": true}'

        @property
        def text(self):
            return '{"ok": true}'

        def json(self):
            return self._json

        @property
        def url(self):
            return httpx.URL("https://www.example.com/final")

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, **kwargs):
            capture["kwargs"] = kwargs
            capture["trust_env"] = kwargs.get("trust_env")
            capture["proxy"] = kwargs.get("proxy")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            return _FakeResponse()

        async def request(self, method, url, **kwargs):
            return _FakeResponse()

    # base.py / pixiv / youtube / weibo / xiaohongshu 都在函数内 from httpx import AsyncClient
    # 所以替换 httpx 模块上的 AsyncClient 即可让所有 ``from httpx import AsyncClient`` 拿到假实现
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


# --------------------------------------------------------------------------- #
# base.py: request() 默认 trust_env=False
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_request_defaults_trust_env_false(monkeypatch):
    """request() 不传 trust_env 时，AsyncClient 收到 trust_env=False。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    parser = BaseParser()
    await parser.request("https://www.example.com/api")

    assert capture["trust_env"] is False, "request() 应默认 trust_env=False"


@pytest.mark.asyncio
async def test_request_trust_env_true_passthrough(monkeypatch):
    """request(trust_env=True) 仍能把 True 透传给 AsyncClient（保留逃生口）。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    parser = BaseParser()
    await parser.request("https://www.example.com/api", trust_env=True)

    assert capture["trust_env"] is True


@pytest.mark.asyncio
async def test_request_proxy_passthrough(monkeypatch):
    """request(proxy=...) 把代理透传给 AsyncClient，且默认 trust_env=False 不影响它。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    parser = BaseParser()
    await parser.request("https://www.example.com/api", proxy="http://my-proxy:8080")

    assert capture["proxy"] == "http://my-proxy:8080"
    assert capture["trust_env"] is False


# --------------------------------------------------------------------------- #
# base.py: get_redirect_url / get_final_url 强制 trust_env=False
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_redirect_url_trust_env_false(monkeypatch):
    """get_redirect_url 创建 AsyncClient 时 trust_env=False（本次故障根因回归）。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    await BaseParser.get_redirect_url("https://b23.tv/AbCd12")
    assert capture["trust_env"] is False, "get_redirect_url 必须 trust_env=False"


@pytest.mark.asyncio
async def test_get_final_url_trust_env_false(monkeypatch):
    """get_final_url 创建 AsyncClient 时 trust_env=False。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    await BaseParser.get_final_url("https://b23.tv/AbCd12")
    assert capture["trust_env"] is False, "get_final_url 必须 trust_env=False"


# --------------------------------------------------------------------------- #
# youtube: _fetch_author_info 显式走配置代理
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_youtube_fetch_author_uses_config_proxy(monkeypatch):
    """YouTube 解析作者信息显式传 proxy=pconfig.proxy（国内不可直连）。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers.youtube import YouTubeParser

    monkeypatch.setattr(pconfig, "parser_proxy", "http://my-proxy:8080")

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    parser = YouTubeParser()
    # _fetch_author_info 内部 decode 响应可能失败，但只关心 AsyncClient 构造参数
    try:
        await parser._fetch_author_info("UC_test")
    except Exception:
        pass  # 假响应不是合法 youtubei JSON，decode 会抛错，但 proxy 已被捕获

    assert capture["proxy"] == "http://my-proxy:8080", "YouTube 应显式走 pconfig.proxy"


@pytest.mark.asyncio
async def test_youtube_fetch_author_no_proxy_when_unconfigured(monkeypatch):
    """未配置 parser_proxy 时，YouTube _fetch_author_info 不传 proxy（不硬编码代理）。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers.youtube import YouTubeParser

    monkeypatch.setattr(pconfig, "parser_proxy", None)

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    parser = YouTubeParser()
    try:
        await parser._fetch_author_info("UC_test")
    except Exception:
        pass

    # pconfig.proxy 为 None 时，proxy 参数为 None（直连）
    assert capture.get("proxy") is None


# --------------------------------------------------------------------------- #
# kugou_api: curl_cffi 显式传 proxies（国内默认空=直连）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_kugou_get_play_url_proxies_empty_when_unconfigured(monkeypatch):
    """未配置 parser_proxy 时，酷狗 curl_cffi 显式传空 proxies（强制直连，不读环境变量）。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import kugou_api

    monkeypatch.setattr(pconfig, "parser_proxy", None)

    captured: dict = {}

    class _FakeResp:
        status_code = 200

        @property
        def headers(self):
            return {}  # 无 ssa-code

        @property
        def text(self):
            import json

            return json.dumps({"errcode": 0, "url": "https://track.kugou.com/test.mp3"})

        def json(self):
            import json

            return json.loads(self.text)

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            captured["proxies"] = kwargs.get("proxies")
            return _FakeResp()

    import sys
    import types

    fake_mod = types.ModuleType("curl_cffi.requests")
    fake_mod.AsyncSession = _FakeSession
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_mod)

    from nonebot_plugin_parser.parsers.base import BaseParser

    parser = BaseParser()
    url = await kugou_api.get_play_url(parser, "fakehash123", "128")
    assert url == "https://track.kugou.com/test.mp3"
    # 关键：未配置代理时，显式传空 proxies（强制直连），不依赖环境变量
    assert captured["proxies"] == {"https": "", "http": ""}


@pytest.mark.asyncio
async def test_kugou_get_play_url_proxies_when_configured(monkeypatch):
    """配置了 parser_proxy 时，酷狗 curl_cffi 走该代理。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import kugou_api

    monkeypatch.setattr(pconfig, "parser_proxy", "http://my-proxy:8080")

    captured: dict = {}

    class _FakeResp:
        status_code = 200

        @property
        def headers(self):
            return {}

        @property
        def text(self):
            import json

            return json.dumps({"errcode": 0, "url": "https://track.kugou.com/test.mp3"})

        def json(self):
            import json

            return json.loads(self.text)

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            captured["proxies"] = kwargs.get("proxies")
            return _FakeResp()

    import sys
    import types

    fake_mod = types.ModuleType("curl_cffi.requests")
    fake_mod.AsyncSession = _FakeSession
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_mod)

    from nonebot_plugin_parser.parsers.base import BaseParser

    parser = BaseParser()
    await kugou_api.get_play_url(parser, "fakehash123", "128")
    assert captured["proxies"] == {"https": "http://my-proxy:8080", "http": "http://my-proxy:8080"}


# --------------------------------------------------------------------------- #
# 回归：weibo / xiaohongshu 删除冗余 trust_env=False 后行为不变（继承默认直连）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_weibo_request_inherits_default_trust_env_false(monkeypatch):
    """weibo 删除冗余 trust_env=False 后，request 仍传 trust_env=False（默认值）。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    parser = BaseParser()
    # 模拟 weibo 的调用方式（不传 trust_env）
    await parser.request(
        "https://m.weibo.cn/statuses/show",
        follow_redirects=False,
        raise_for_status=False,
    )
    assert capture["trust_env"] is False


# --------------------------------------------------------------------------- #
# base.py: get_redirect_url / get_final_url 的 proxy 参数（海外平台短链重定向）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_redirect_url_proxy_passthrough(monkeypatch):
    """get_redirect_url(proxy=...) 把代理透传给 AsyncClient。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    await BaseParser.get_redirect_url("https://vt.tiktok.com/AbCd12", proxy="http://my-proxy:8080")
    assert capture["proxy"] == "http://my-proxy:8080"
    assert capture["trust_env"] is False  # 即便走了代理，trust_env 仍为 False


@pytest.mark.asyncio
async def test_get_redirect_url_default_no_proxy(monkeypatch):
    """get_redirect_url 不传 proxy 时，AsyncClient 不带 proxy（国内短链直连）。"""
    from nonebot_plugin_parser.parsers.base import BaseParser

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    await BaseParser.get_redirect_url("https://b23.tv/AbCd12")
    assert capture.get("proxy") is None
    assert capture["trust_env"] is False


# --------------------------------------------------------------------------- #
# tiktok: 短链重定向显式走配置代理（国内不可直连）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_tiktok_short_link_uses_config_proxy(monkeypatch):
    """TikTok vt/vm 短链重定向显式传 proxy=pconfig.proxy。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers.tiktok import TikTokParser

    monkeypatch.setattr(pconfig, "parser_proxy", "http://my-proxy:8080")

    capture: dict = {}
    _patch_asyncclient(monkeypatch, capture)

    parser = TikTokParser()
    # 匹配 vt.tiktok.com 短链，触发 get_redirect_url 分支
    searched = parser.search_url("https://vt.tiktok.com/AbCd12/")[1]
    try:
        await parser._parse(searched)
    except Exception:
        pass  # ytdlp 提取会失败，但 get_redirect_url 的 proxy 已被捕获

    assert capture["proxy"] == "http://my-proxy:8080", "TikTok 短链重定向应显式走 pconfig.proxy"
