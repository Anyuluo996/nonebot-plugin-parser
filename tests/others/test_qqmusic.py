"""QQ音乐适配层纯单元测试（不打网络）。

引擎定位、设备指纹持久化、QRC→LRC 转换、凭证持久化、内置实现回退路径。
真实网络测试见 ``tests/parsers/test_qqmusic_network.py``。
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def qqapi():
    """取适配层模块（延后到 nonebot 初始化之后再导入）。"""
    from nonebot_plugin_parser.parsers.qqmusic import api as qqmusic_api

    return qqmusic_api


@pytest.fixture
def qqcred(qqapi):
    """取凭证模块。"""
    from nonebot_plugin_parser.parsers.qqmusic import credential as cred

    return cred


def _require_engine(qqapi) -> None:
    """增强引擎未安装时跳过（引擎专属路径）。"""
    if qqapi.protocol is None:
        pytest.skip("增强引擎未安装，当前走内置实现")


# --------------------------------------------------------------------------- #
# 引擎定位
# --------------------------------------------------------------------------- #
def test_parser_available(qqapi) -> None:
    """QQ 音乐功能必须可用（引擎或内置实现至少其一）。"""
    from nonebot_plugin_parser.parsers import _QQMUSIC_AVAILABLE

    assert _QQMUSIC_AVAILABLE


def test_candidate_paths_includes_repo(qqapi) -> None:
    """同级（或上两级 app/）的引擎源码目录应被识别为候选。"""
    paths = [str(p).replace("\\", "/").lower() for p in qqapi._candidate_paths()]
    assert any("qqmusic-protocol" in p for p in paths), paths


def test_env_path_takes_priority(qqapi, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(qqapi.ENV_PROTOCOL_PATH, str(tmp_path))
    assert str(qqapi._candidate_paths()[0]) == str(tmp_path)


def test_load_protocol_silent_when_missing(qqapi, tmp_path, monkeypatch) -> None:
    """引擎缺失必须静默返回 None（回退内置实现），不能抛异常。"""
    import sys

    monkeypatch.delenv(qqapi.ENV_PROTOCOL_PATH, raising=False)
    monkeypatch.setattr(qqapi, "_candidate_paths", lambda: [tmp_path / "nope"])
    monkeypatch.setitem(sys.modules, "qqmusic_protocol", None)  # type: ignore[arg-type]
    assert qqapi._load_protocol() is None


# --------------------------------------------------------------------------- #
# 设备指纹（引擎专属）
# --------------------------------------------------------------------------- #
def test_device_fingerprint_is_stable(qqapi) -> None:
    """设备指纹必须跨调用稳定（服务端做一致性校验，不能每次换）。"""
    _require_engine(qqapi)
    dev = qqapi.current_device()
    assert dev.android_id and dev.boot_id
    assert dev.udid.startswith("ffffffff")
    assert len(dev.udid) == 32
    assert qqapi.current_device() is dev


def test_device_round_trip(qqapi, tmp_path) -> None:
    """设备指纹应能落盘并原样读回。"""
    _require_engine(qqapi)
    dev = qqapi.current_device()
    path = tmp_path / "qqmusic_device.json"
    path.write_text(
        json.dumps(dev.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reloaded = type(dev).from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert reloaded.udid == dev.udid
    assert reloaded.android_id == dev.android_id
    assert reloaded.open_udid2 == dev.open_udid2
    assert reloaded.imei == dev.imei


# --------------------------------------------------------------------------- #
# QRC → LRC（纯文本处理，两条路径通用）
# --------------------------------------------------------------------------- #
class TestQrcToLrc:
    # 真实 QRC 结构（实测抓包）：
    #   - 元素名是 <Lyric_1>，不是 <Lyric>
    #   - 行级时间戳 [行起始毫秒,行持续毫秒]
    #   - 逐字时间戳 (相对毫秒,持续毫秒)，跟在每个字符**后面**
    #   - LyricContent 里还夹着标准 LRC 元信息头
    QRC = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<QrcInfos>\n"
        '<QrcHeadInfo SaveTime="269" Version="100"/>\n'
        '<LyricInfo LyricCount="1">\n'
        '<Lyric_1 LyricType="1" LyricContent="[ti:晴天]\n'
        "[ar:周杰伦]\n"
        "[offset:0]\n"
        "[0,2250]天(0,160)气(160,160) (320,160)晴(640,160)朗(800,160)\n"
        '[2250,2250]故(2250,450)事(2700,450)\n'
        '">\n'
        "</LyricInfo>\n"
        "</QrcInfos>"
    )

    def test_basic(self, qqapi) -> None:
        lrc = qqapi.protocol_qrc_to_lrc(self.QRC)
        lines = lrc.splitlines()
        assert lines[0] == "[ti:晴天]"
        assert lines[1] == "[ar:周杰伦]"
        assert lines[2] == "[offset:0]"
        assert lines[3] == "[00:00.00]天气 晴朗"
        assert lines[4] == "[00:02.25]故事"
        assert len(lines) == 5

    def test_strips_per_char_timing(self, qqapi) -> None:
        """逐字时间戳必须被剥掉，只留行级。"""
        lrc = qqapi.protocol_qrc_to_lrc(self.QRC)
        assert "(" not in lrc
        assert "2250" not in lrc.replace("[offset:0]", "")

    def test_keeps_lyric_parentheses(self, qqapi) -> None:
        """歌词正文里的普通括号（(DJ版)）不能被当成时间戳删掉。"""
        qrc = (
            '<QrcInfos><Lyric_1 LyricType="1" LyricContent="'
            "[0,1000]版本(2020)(0,1000)A(1000,1000)"
            '"></Lyric_1></QrcInfos>'
        )
        # (2020) 不是 (数字,数字) 形式，应保留
        assert "(2020)" in qqapi.protocol_qrc_to_lrc(qrc)

    def test_ms_conversion(self, qqapi) -> None:
        assert qqapi._ms_to_lrc(0) == "00:00.00"
        assert qqapi._ms_to_lrc(1_000) == "00:01.00"
        assert qqapi._ms_to_lrc(61_500) == "01:01.50"
        assert qqapi._ms_to_lrc(-5) == "00:00.00"

    def test_handles_leading_garbage(self, qqapi) -> None:
        """服务端偶尔在 XML 前塞杂字符，不能因此整段丢失。"""
        noisy = "some prefix junk\n" + self.QRC
        assert "天气 晴朗" in qqapi.protocol_qrc_to_lrc(noisy)

    def test_fallback_on_broken_xml(self, qqapi) -> None:
        """XML 残缺时走正则兜底。"""
        broken = '<QrcInfos><Lyric_1 LyricType="1" LyricContent="[0,100]hello(0,100)">'
        assert qqapi.protocol_qrc_to_lrc(broken) == "[00:00.00]hello"

    def test_xml_entity_unescaped(self, qqapi) -> None:
        """XML 实体要走 ET 解析才能正确反转义。"""
        qrc = (
            '<QrcInfos><Lyric_1 LyricType="1" LyricContent="'
            "[0,100]Rock &amp; Roll(0,100)"
            '"></Lyric_1></QrcInfos>'
        )
        assert "Rock & Roll" in qqapi.protocol_qrc_to_lrc(qrc)

    def test_skips_translation_lyric_type(self, qqapi) -> None:
        """LyricType=0 是翻译，不该混进主歌词。"""
        qrc = (
            "<QrcInfos>"
            '<Lyric_1 LyricType="0" LyricContent="[0,100]翻译(0,100)"/>'
            '<Lyric_2 LyricType="1" LyricContent="[0,100]主歌(0,100)"/>'
            "</QrcInfos>"
        )
        out = qqapi.protocol_qrc_to_lrc(qrc)
        assert "主歌" in out
        assert "翻译" not in out

    def test_empty(self, qqapi) -> None:
        assert qqapi.protocol_qrc_to_lrc("") == ""
        assert qqapi.protocol_qrc_to_lrc("   ") == ""

    def test_no_timing_lines(self, qqapi) -> None:
        assert qqapi.protocol_qrc_to_lrc("<QrcInfos></QrcInfos>") == ""


# --------------------------------------------------------------------------- #
# 凭证持久化
# --------------------------------------------------------------------------- #
def test_anonymous_when_no_file(qqcred) -> None:
    cred = qqcred.load_credential_raw()
    assert not qqcred.is_available()
    # 引擎模式返回匿名凭证；内置模式返回 None
    assert cred is None or not cred.logged_in


def test_save_and_load_round_trip(qqapi, qqcred) -> None:
    _require_engine(qqapi)
    cred = qqapi.protocol.Credential(musickey="TESTKEY", musicid=12345, qq="12345")
    qqcred.save_credential(cred)
    try:
        assert qqcred.is_available()
        loaded = qqcred.load_credential_raw()
        assert loaded.musickey == "TESTKEY"
        assert loaded.musicid == 12345
    finally:
        qqcred.clear_credential()
    assert not qqcred.is_available()


def test_save_fills_time_fields(qqapi, qqcred) -> None:
    """扫码库不写有效期字段，这里补上，否则加载时被误判过期。"""
    _require_engine(qqapi)
    qqcred.save_credential(qqapi.protocol.Credential(musickey="K2"))
    try:
        loaded = qqcred.load_credential_raw()
        assert loaded.musickey_create_time > 0
        assert loaded.key_expires_in > 0
        assert not loaded.is_expired()
    finally:
        qqcred.clear_credential()


def test_accepts_legacy_qqmusic_api_object(qqcred) -> None:
    """兼容扫码库（qqmusic-api-python）产出的对象形态。"""

    class Legacy:
        musicid = 999
        musickey = "LEGACY"
        refresh_key = "RK"
        str_musicid = "999"
        encrypt_uin = ""
        login_type = 2
        access_token = ""
        openid = ""
        unionid = ""
        wxopenid = ""
        qq = "999"
        refresh_token = ""
        nickname = "tester"

    qqcred.save_credential(Legacy())
    try:
        loaded = qqcred.load_credential_raw()
        assert loaded.musickey == "LEGACY"
        assert loaded.musicid == 999
    finally:
        qqcred.clear_credential()


def test_clear_credential_returns_bool(qqapi, qqcred) -> None:
    assert qqcred.clear_credential() is False
    if qqapi.protocol is not None:
        qqcred.save_credential(qqapi.protocol.Credential(musickey="K3"))
    else:
        qqcred.save_credential({"musickey": "K3"})
    assert qqcred.clear_credential() is True
    qqcred.clear_credential()


def test_expired_credential_degrades_to_anonymous(qqapi, qqcred) -> None:
    """过期凭证必须降级匿名，而不是拿一个必然 104003 的登录态去请求。"""
    _require_engine(qqapi)
    cred = qqapi.protocol.Credential(musickey="OLD")
    qqcred.save_credential(cred)
    try:
        loaded = qqcred.load_credential_raw()
        # 把创建时间改到很久以前
        object.__setattr__(loaded, "musickey_create_time", 1)
        assert loaded.is_expired()
    finally:
        qqcred.clear_credential()


def test_parse_ck(qqcred) -> None:
    if qqcred._engine() is None:
        pytest.skip("parse_ck 仅引擎模式支持")
    cred = qqcred.parse_ck("musickey=CKKEY; refresh_key=R; musicid=42")
    assert cred.musickey == "CKKEY"
    assert cred.logged_in


# --------------------------------------------------------------------------- #
# 降级
# --------------------------------------------------------------------------- #
def test_parser_registered() -> None:
    from nonebot_plugin_parser.parsers import _QQMUSIC_AVAILABLE, PARSERS

    if _QQMUSIC_AVAILABLE:
        assert "qqmusic" in PARSERS, list(PARSERS)


def test_reset_client_is_idempotent(qqapi) -> None:
    qqapi.reset_client()
    qqapi.reset_client()


def test_search_graceful_when_client_unavailable(qqapi, monkeypatch) -> None:
    """拿不到客户端时搜索返回空列表而不是抛异常（点歌是三服务并发）。"""
    import asyncio

    monkeypatch.setattr(qqapi, "_get_client", lambda: None)
    assert asyncio.run(qqapi.search_songs("晴天", 3)) == []


def test_get_play_url_requires_media_mid(qqapi, monkeypatch) -> None:
    """media_mid 为空必须直接返回 None（拼不出加密容器名）。"""
    import asyncio

    class FakeClient:
        async def cdn_hosts(self):
            return ["http://x/"]

    monkeypatch.setattr(qqapi, "_get_client", lambda: FakeClient())
    assert asyncio.run(qqapi.get_play_url(None, "mid", "")) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 内置实现回退路径（stub 掉 qqmusic_api.Client，不打网络）
# --------------------------------------------------------------------------- #
class _Stub:
    """任意属性的对象。"""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def _make_client_stub(
    track: Any = None,
    lyric_text: str | None = None,
    search_items: list[Any] | None = None,
    purl: str = "M800xxx.mp3",
) -> type:
    class _Song:
        async def get_detail(self, mid: str):
            return _Stub(track=track)

        async def get_cdn_dispatch(self):
            return _Stub(sip=["http://cdn.test/"])

        async def get_song_urls(self, infos, credential=None):
            return _Stub(data=[_Stub(result=0, purl=purl)])

    class _Lyric:
        async def get_lyric(self, mid: str):
            return _Stub(lyric=lyric_text)

    class _Search:
        async def general_search(self, kw, page=1, num=30):
            return _Stub(song=_Stub(items=search_items))

    class _Client:
        def __init__(self, credential=None) -> None:
            self.song = _Song()
            self.lyric = _Lyric()
            self.search = _Search()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    return _Client


def test_fallback_detail(qqapi, monkeypatch) -> None:
    """引擎缺失时详情走内置实现，字段映射对齐协议路径。"""
    import asyncio

    track = _Stub(
        name="晴天",
        title=None,
        singer=[_Stub(name="周杰伦")],
        interval=269,
        mid="002bS8mS3VKxaY",
        file=_Stub(media_mid="32123456"),
        cover_url=lambda size: "http://pic.test/x.jpg",
    )
    monkeypatch.setattr(qqapi, "protocol", None)
    monkeypatch.setattr("qqmusic_api.Client", _make_client_stub(track=track))

    detail = asyncio.run(qqapi.get_song_detail(None, "002bS8mS3VKxaY"))  # type: ignore[arg-type]
    assert detail["name"] == "晴天"
    assert detail["artist"] == "周杰伦"
    assert detail["pic_url"] == "http://pic.test/x.jpg"
    assert detail["duration"] == 269.0
    assert detail["media_mid"] == "32123456"


def test_fallback_play_url(qqapi, monkeypatch) -> None:
    """引擎缺失时取链走内置实现，返回直链 str（不是 Task）。"""
    import asyncio

    monkeypatch.setattr(qqapi, "protocol", None)
    monkeypatch.setattr("qqmusic_api.Client", _make_client_stub(purl="abc/M800xxx.mp3"))

    got = asyncio.run(qqapi.get_play_url(None, "mid", "mmid"))  # type: ignore[arg-type]
    assert isinstance(got, str)
    assert got == "http://cdn.test/abc/M800xxx.mp3"


def test_fallback_lyric(qqapi, monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(qqapi, "protocol", None)
    monkeypatch.setattr("qqmusic_api.Client", _make_client_stub(lyric_text="[00:00.00]hi"))

    assert asyncio.run(qqapi.get_lyric(None, "mid")) == "[00:00.00]hi"  # type: ignore[arg-type]


def test_fallback_search(qqapi, monkeypatch) -> None:
    """引擎缺失时搜索走内置实现，dict 形状与协议路径一致。"""
    import asyncio

    item = _Stub(
        mid="003xxx",
        name="晴天",
        title=None,
        singer=[_Stub(name="周杰伦")],
        interval=269,
        album=_Stub(cover_url=lambda: "http://pic.test/a.jpg"),
        pay=_Stub(pay_play=1),
        file=_Stub(media_mid="mmid1"),
    )
    monkeypatch.setattr(qqapi, "protocol", None)
    monkeypatch.setattr("qqmusic_api.Client", _make_client_stub(search_items=[item]))

    songs = asyncio.run(qqapi.search_songs("晴天", 3))
    assert len(songs) == 1
    assert songs[0]["mid"] == "003xxx"
    assert songs[0]["media_mid"] == "mmid1"
    assert songs[0]["artist"] == "周杰伦"
    assert songs[0]["is_paid"] is True


def test_fallback_credential_none_when_missing(qqcred, tmp_path, monkeypatch) -> None:
    """内置模式下无登录态返回 None（而非匿名凭证）。"""
    from nonebot_plugin_parser.parsers.qqmusic import api as qqapi_mod

    monkeypatch.setattr(qqapi_mod, "protocol", None)
    monkeypatch.setattr(qqcred, "_CRED_FILE", tmp_path / "no_cred.json")
    assert qqcred.load_credential_raw() is None
    assert not qqcred.is_available()


# --------------------------------------------------------------------------- #
# 数字 song_id 路由（短链 songDetail/<数字> 场景，引擎专属）
# --------------------------------------------------------------------------- #
class _FakeSong:
    """duck-typing 引擎 Song 的最小桩。"""

    mid = "00128N3r2SYKMF"
    media_mid = "003VLsik0ztbIb"
    vs_pic = "https://example.test/cover.jpg"
    name = "兰亭序"
    title = "兰亭序"
    artist = "周杰伦"
    duration = 253
    interval = 253


def test_numeric_song_id_routes_to_track_by_id(qqapi, monkeypatch) -> None:
    """纯数字必须走曲库 ids 反查，不能当 mid 反查或当搜索词。"""
    _require_engine(qqapi)
    import asyncio

    calls: list[int] = []

    async def fake_track_by_id(client, song_id):
        calls.append(song_id)
        return _FakeSong()

    async def fail_search(*a, **k):
        raise AssertionError("数字 id 不应走搜索")

    async def fail_track_by_mid(*a, **k):
        raise AssertionError("数字 id 不应走 mids 反查")

    monkeypatch.setattr(qqapi._qp_api, "track_by_id", fake_track_by_id)
    monkeypatch.setattr(qqapi._qp_api, "search", fail_search)
    monkeypatch.setattr(qqapi._qp_api, "track_by_mid", fail_track_by_mid)

    detail = asyncio.run(qqapi.get_song_detail(None, "449201"))  # type: ignore[arg-type]
    assert calls == [449201]
    assert detail["name"] == "兰亭序"
    assert detail["song_mid"] == "00128N3r2SYKMF"
    assert detail["media_mid"] == "003VLsik0ztbIb"


def test_mid_still_routes_via_search(qqapi, monkeypatch) -> None:
    """非数字 mid 保持原路径：先搜索定位，失败退 mids 反查。"""
    _require_engine(qqapi)
    import asyncio

    called: list[str] = []

    class _Resp:
        songs: ClassVar[list] = []

    async def fake_search(client, kw, limit=5):
        called.append(f"search:{kw}")
        return _Resp()

    async def fail_track_by_id(client, song_id):
        raise AssertionError("非数字 mid 不应走 ids 反查")

    async def fake_track_by_mid(client, mid):
        called.append(f"mid:{mid}")
        return _FakeSong()

    monkeypatch.setattr(qqapi, "_ensure_qimei", _noop_async)
    monkeypatch.setattr(qqapi._qp_api, "search", fake_search)
    monkeypatch.setattr(qqapi._qp_api, "track_by_id", fail_track_by_id)
    monkeypatch.setattr(qqapi._qp_api, "track_by_mid", fake_track_by_mid)

    detail = asyncio.run(qqapi.get_song_detail(None, "00128N3r2SYKMF"))  # type: ignore[arg-type]
    assert called == ["search:00128N3r2SYKMF", "mid:00128N3r2SYKMF"]
    assert detail["name"] == "兰亭序"


async def _noop_async(*a, **k):
    return None
