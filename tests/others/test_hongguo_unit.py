"""红果短剧 parser 纯单元测试（不依赖网络）。"""

import json

import pytest


def test_sign_request_with_body():
    """X-Gorgon 签名向量（来源: 实测可用的实现基线，改动须保持逐字节一致）。"""
    from nonebot_plugin_parser.parsers.hongguo._sign import sign_request

    query = "aid=8662&app_name=novelread&device_id=3612361568583113&_rticket=1791459784000"
    body = b'{"video_id":123,"content_type":1}'
    headers = sign_request(query, body, 1791459784.0)

    assert headers["X-Khronos"] == "1791459784"
    assert headers["X-SS-Req-Ticket"] == "1791459784000"
    assert headers["X-SS-STUB"] == "F55B115B8A45F404EFE2463AF58E9595"
    assert headers["X-Gorgon"] == "8404401c0000aa8ea9dcec067f907022103fad22c929560b8d90"


def test_sign_request_without_body():
    from nonebot_plugin_parser.parsers.hongguo._sign import sign_request

    query = "aid=8662&app_name=novelread&device_id=3612361568583113&_rticket=1791459784000"
    headers = sign_request(query, None, 1791459784.0)

    assert "X-SS-STUB" not in headers
    assert headers["X-Gorgon"] == "8404401c0000aa8ea973cc232d3d7022103fad22c929560b8d90"


def test_derive_content_key():
    """真实 spade_a 样本（2026-10-08 抓取）→ AES-128 内容密钥。"""
    from nonebot_plugin_parser.parsers.hongguo._crypto import derive_content_key

    spade_a = "nbwT9W+yJsldgRP6XrAn/HezPfRAjQvxRYw59XO8OMdHjg+wsA=="
    assert derive_content_key(spade_a).hex() == "03e12dc27d53a47930eb3d5fda112be3"


def test_derive_content_key_invalid():
    from nonebot_plugin_parser.parsers.hongguo._crypto import derive_content_key

    with pytest.raises(ValueError, match="结构无效|过短"):
        derive_content_key("aGVoZQ==")  # "hehe"，结构必然无效


def test_parse_router_data():
    from nonebot_plugin_parser.parsers.hongguo import _parse_router_data

    text = '<html><script>window._ROUTER_DATA = {"loaderData": {"detail_page": {"a": 1}}};</script>'
    data = _parse_router_data(text)
    assert data == {"loaderData": {"detail_page": {"a": 1}}}
    # 尾部有其它脚本内容时只取首个 JSON 值
    text2 = text.replace("</script>", "</script><script>var x = 1; console.log(1, 2);</script>")
    assert _parse_router_data(text2) == data
    assert _parse_router_data("<html>no data</html>") is None


def test_parse_scheme_params():
    from urllib.parse import quote

    from nonebot_plugin_parser.parsers.hongguo import _parse_scheme_params

    raw = json.dumps({"video_series_id": "123", "vid": "456"})
    assert _parse_scheme_params(raw) == {"video_series_id": "123", "vid": "456"}
    # 单层 URL 编码
    assert _parse_scheme_params(quote(raw)) == {"video_series_id": "123", "vid": "456"}
    # 双层 URL 编码
    assert _parse_scheme_params(quote(quote(raw))) == {"video_series_id": "123", "vid": "456"}
    assert _parse_scheme_params("not-json") == {}


def test_extract_ids_from_params():
    from nonebot_plugin_parser.parsers.hongguo import _extract_ids_from_params

    # 真实漫剧分享形态: series_id 为空
    assert _extract_ids_from_params(
        {"video_series_id": "", "vs_id_type": "1", "vid": "7678043574918728729", "video_id": "7677976226299907134"}
    ) == ("", "7678043574918728729")
    # 真人剧分享形态
    assert _extract_ids_from_params({"video_series_id": "7687867423457823768", "vid": "7687869271090678808"}) == (
        "7687867423457823768",
        "7687869271090678808",
    )
    assert _extract_ids_from_params({}) == ("", "")


def test_find_page_data():
    from html import escape

    from nonebot_plugin_parser.parsers.hongguo import _find_page_data

    page_data = [{"key": "pageData", "routerDataFnName": "s"}]
    target = ["video-list-share-ssr_page", "pageData", {"series_data": {"title": "测试剧"}, "chapter_ids": ["1", "2"]}]
    html_text = (
        f'<div hidden id="S:1"><script async data-fn-args="{escape(json.dumps(page_data))}"></script>'
        f'<script data-fn-name="r" data-fn-args="{escape(json.dumps(target))}"></script></div>'
    )
    page = _find_page_data(html_text)
    assert page is not None
    assert page["series_data"]["title"] == "测试剧"


def test_search_url_patterns():
    """URL / 文本匹配形态覆盖。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser
    cases = [
        ("https://novelquickapp.com/s/oDVSKBzlB9U/", "novelquickapp.com/s/"),
        ("https://novelquickapp.com/s/oDVSKBzlB9U", "novelquickapp.com/s/"),
        (
            "https://novelquickapp.com/hongguo/ug/pages/video-list-share?zlink=x",
            "novelquickapp.com/hongguo",
        ),
        (
            "https://applink.novelquickapp.com/dVu4P?schemeParams=%7B%22vid%22%3A%221%22%7D",
            "applink.novelquickapp",
        ),
        ("https://hongguoduanju.com/detail?series_id=7689336045552274457", "hongguoduanju.com"),
        ("https://hongguoduanju.com/player/7689336045552274457/7689337797118807065", "hongguoduanju.com"),
        ("漫剧《人在诡异，靠第四天灾登顶至高》 - 免费好剧，尽在红果", "免费好剧，尽在红果"),
    ]
    for text, _keyword in cases:
        keyword, searched = parser.search_url(text)
        assert searched, f"未匹配: {text}"
        assert keyword == _keyword, f"{text} 命中 {keyword} 而非 {_keyword}"


def test_media_addresses_base64():
    import base64

    from nonebot_plugin_parser.parsers.hongguo import _media_addresses

    encoded = base64.b64encode(b"https://example.com/v.mp4").decode()
    info = {"main_url": "https://a.com/1.mp4", "backup_url_1": encoded, "bad": "///"}
    addresses = _media_addresses(info)
    assert addresses == ["https://a.com/1.mp4", "https://example.com/v.mp4"]
