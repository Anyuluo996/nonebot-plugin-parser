"""红果短剧 parser 真实网络测试（tests/parsers/ 自动带 network marker）。"""

import re
import json
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

    该日上游收紧后旧 App video_model 一律 200+空 body、web player 页不再内嵌
    播放数据; 当日已接入游客 multi_video_model 渠道恢复 (形态逆向自
    hongguo-mac v0.8.1)。本守卫保留: 若三渠道再次整体失效 (取流全灭特征
    文案) 则 skip, 其余异常原样上抛, 不掩盖解析层回归。
    """
    text = str(e)
    if "红果播放接口无有效响应" in text or "红果网页播放数据为空" in text:
        pytest.skip(f"红果上游取流渠道全灭 (待重新逆向): {text}")


# === 离线单测: 游客播放请求构造 / video_model 轨道选择 ===


def test_guest_playback_request_shape():
    """multi_video_model 请求构造: 随机身份、免签名头、mixed_video_id_map body。"""
    from nonebot_plugin_parser.parsers.hongguo import _guest_playback_request

    url, headers, body = _guest_playback_request(["7687869271090678808"])
    assert "multi_video_model/v1/" in url, f"应指向游客批量播放端点: {url}"
    assert "sinfonlinec" in url, "2026-10 改版后 host 为 sinfoninec"
    # 身份字段为随机 18 位数字, 每次构造不同
    assert re.search(r"iid=(\d{18})", url)
    assert re.search(r"device_id=(\d{18})", url)
    assert _guest_playback_request(["7687869271090678808"])[0] != url
    # 免签名免凭据: 无 X-Gorgon/X-Argus/Cookie/x-ss-stub
    signed_headers = {"x-gorgon", "x-argus", "x-ladon", "x-khronos", "cookie", "x-ss-stub"}
    assert not signed_headers & {k.lower() for k in headers}, f"游客渠道不应携带签名/凭据头: {headers.keys()}"
    assert headers["sdk-version"] == "2"
    payload = json.loads(body)
    assert payload["mixed_video_id_map"] == {"1004": ["7687869271090678808"]}
    assert payload["biz_param"]["video_platform"] == 1024
    assert payload["biz_param"]["need_all_video_definition"] is True


def test_media_from_video_model_picks_highest():
    """轨道选择取最高清晰度, 毫秒 duration 落到 _Media, 明文轨 key_hex=None。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    model = {
        "video_duration": "15000",
        "video_list": [
            {"main_url": "https://cdn.example/low.mp4", "video_meta": {"vheight": 360}},
            {"main_url": "https://cdn.example/high.mp4", "video_meta": {"vheight": 1080}},
            {"main_url": "https://cdn.example/mid.mp4", "video_meta": {"vheight": 720}},
        ],
    }
    media = parser._media_from_video_model(model)
    assert media.url == "https://cdn.example/high.mp4"
    assert media.key_hex is None
    assert media.duration == 15000.0
    assert media.backup_urls == []


def test_media_from_video_model_bad_spade_raises():
    """轨道带非法 spade_a 时报密钥不可用, 而非静默选明文轨。"""
    from nonebot_plugin_parser.parsers import HongguoParser
    from nonebot_plugin_parser.exception import ParseException

    parser = HongguoParser()
    model = {"video_list": [{"main_url": "https://cdn.example/a.mp4", "encrypt_info": {"spade_a": "!!!"}}]}
    with pytest.raises(ParseException, match="密钥不可用"):
        parser._media_from_video_model(model)


def test_guest_episode_request_shape():
    """multi_video_detail 请求构造: 免签名头 + series_id 在 body。"""
    from nonebot_plugin_parser.parsers.hongguo import _guest_episode_request

    url, headers, body = _guest_episode_request("7687867423457823768")
    assert "multi_video_detail/v1/" in url
    assert "sinfonlinec" in url
    assert re.search(r"iid=(\d{18})", url)
    signed_headers = {"x-gorgon", "x-argus", "x-ladon", "cookie", "x-ss-stub"}
    assert not signed_headers & {k.lower() for k in headers}, f"游客渠道不应携带签名/凭据头: {headers.keys()}"
    payload = json.loads(body)
    assert payload["series_id"] == "7687867423457823768"
    assert payload["biz_param"]["source"] == 7


def test_parse_episode_index_map():
    """剧集列表解析: list/dict 双形态 + 病态输入返回 None。"""
    from nonebot_plugin_parser.parsers.hongguo import _parse_episode_index_map

    rows = [
        {"vid": "7681", "vid_index": 1},
        {"vid": "7682", "vid_index": 2},
        {"vid": None, "vid_index": 3},  # 无 vid 的行跳过
        {"vid": "bad", "vid_index": "x"},  # 集号非数字跳过
    ]
    as_list = {"data": {"sid": {"video_data": {"video_list": rows, "episode_cnt": 68}}}}
    index_map, cnt = _parse_episode_index_map(as_list, "sid")
    assert index_map == {"7681": 1, "7682": 2}
    assert cnt == 68
    # dict 变体 (键即 vid)
    as_dict = {
        "data": {"sid": {"video_data": {"video_list": {"7681": {"vid": "7681", "vid_index": 1}}, "episode_cnt": 9}}}
    }
    index_map, cnt = _parse_episode_index_map(as_dict, "sid")
    assert index_map == {"7681": 1}
    assert cnt == 9
    # 形状不符 → None
    assert _parse_episode_index_map({}, "sid") is None
    assert _parse_episode_index_map({"data": {}}, "sid") is None
    assert _parse_episode_index_map({"data": {"sid": {"video_data": {"video_list": []}}}}, "sid") is None


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
async def test_full_episode_via_guest_api():
    """深层分集经游客 multi_video_model 接口 + CENC 解密拿到可播放 mp4。

    2026-10-17 起主力渠道为免签名免注册的游客批量接口 (mixed_video_id_map
    type=1004), 全集可用; 解密链 (spade_a → AES-128-CTR) 不变。
    除上游整体失效外的一切异常照常 raise (原「无 skip 降级」原则,
    _skip_if_upstream_dead 只命中三渠道全灭特征)。

    集号断言: 该 vid 权威 vid_index=2 (全 120 集, multi_video_detail 实测)。
    历史名称里的「第 51 集」是网页 vid_list 推荐序造成的错位——恰为本功能
    要修复的问题; 上游重排剧集会破坏此钉值, 失败时先核对 authoritative index。
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
        assert "第2集" in (result.title or ""), f"标题应含权威集号: {result.title}"
        path = await result.video_contents[0].get_path()
        assert path.exists()
        assert path.stat().st_size > 0
    finally:
        _cancel_pending(result)
