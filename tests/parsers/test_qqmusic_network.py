"""QQ音乐真实网络端到端测试（App 协议直连）。

被 conftest 自动打上 ``network`` 标记，可用 ``-m "not network"`` 跳过。
CI 绿不等于解析可用 —— 各接口受风控影响，取不到时按需 skip。

⚠️ 本文件**不能**在模块级 import nonebot_plugin_parser：conftest 的
``init_nonebot`` 是 session 级 autouse fixture，在 collection 之后才执行
``nonebot.init()``，模块级导入会撞 "NoneBot has not been initialized"。
统一走 fixture 延迟导入。
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def qqapi():
    """QQ音乐适配层。"""
    from nonebot_plugin_parser.parsers.qqmusic import api

    return api


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_songs(self, qqapi) -> None:
        """真实搜索：应能拿到歌曲且带 mid。"""
        songs = await qqapi.search_songs("晴天", limit=3)
        if not songs:
            pytest.skip("搜索返回 0 条（QIMEI 未申请成功或被限流）")
        s = songs[0]
        assert s["mid"], "缺少 mid"
        assert s["name"]
        assert s["artist"]
        assert s["duration"] > 0

    @pytest.mark.asyncio
    async def test_search_blank_keyword(self, qqapi) -> None:
        assert await qqapi.search_songs("   ") == []

    @pytest.mark.asyncio
    async def test_song_order_search(self) -> None:
        """点歌聚合层：QQ 音乐分支应能出候选。"""
        from nonebot_plugin_parser.music_search import search_qqmusic
        from nonebot_plugin_parser.parsers.netease import NCMParser

        items = await search_qqmusic(NCMParser(), "晴天", limit=3)
        if not items:
            pytest.skip("搜索返回 0 条")
        assert all(i.platform == "qqmusic" for i in items)
        assert all(i.song_id for i in items)


class TestDetail:
    @pytest.mark.asyncio
    async def test_track_lookup(self, qqapi) -> None:
        """真实曲库反查：必须拿到 media_mid（拼加密文件名必需）。"""
        songs = await qqapi.search_songs("晴天", limit=1)
        if not songs:
            pytest.skip("搜索返回 0 条")
        detail = await qqapi.get_song_detail(None, songs[0]["mid"])  # type: ignore[arg-type]
        assert detail, "曲库反查返回空"
        assert detail["media_mid"], "缺少 media_mid，无法拼加密容器名"
        assert detail["song_mid"]
        assert detail["duration"] > 0

    @pytest.mark.asyncio
    async def test_unknown_song_returns_empty(self, qqapi) -> None:
        assert await qqapi.get_song_detail(None, "NOT_A_REAL_MID_123") == {}  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_numeric_song_id(self, qqapi) -> None:
        """短链重定向常落**数字 song_id** 形态 songDetail 页（兰亭序=449201）。

        纯数字必须走曲库 ``ids`` 反查——拿数字串当 mid 或搜索词都查不到。
        """
        detail = await qqapi.get_song_detail(None, "449201")  # type: ignore[arg-type]
        if not detail:
            pytest.skip("曲库反查返回空（限流/风控）")
        assert detail["name"] == "兰亭序"
        assert detail["song_mid"] == "00128N3r2SYKMF"
        assert detail["media_mid"], "缺少 media_mid"
        assert detail["pic_url"], "应能按专辑 mid 拼出封面 URL"


class TestLyric:
    @pytest.mark.asyncio
    async def test_lyric_decrypt(self, qqapi) -> None:
        """真实歌词：应能拿到解密后的 LRC（验证 QRC 改版 DES 全链路）。"""
        songs = await qqapi.search_songs("晴天", limit=1)
        if not songs:
            pytest.skip("搜索返回 0 条")
        lrc = await qqapi.get_lyric(None, songs[0]["mid"])  # type: ignore[arg-type]
        assert lrc, "歌词为空（重点排查 QrcCrypt）"
        assert lrc.lstrip().startswith("["), f"不是 LRC 格式: {lrc[:80]!r}"

    @pytest.mark.asyncio
    async def test_lyric_blank_mid(self, qqapi) -> None:
        assert await qqapi.get_lyric(None, "") == ""  # type: ignore[arg-type]


class TestPlayUrl:
    @pytest.mark.asyncio
    async def test_free_song_url(self, qqapi) -> None:
        """免费歌曲匿名应能拿到播放链接（免费档 640k OGG）。"""
        songs = await qqapi.search_songs("晴天", limit=1)
        if not songs:
            pytest.skip("搜索返回 0 条")
        detail = await qqapi.get_song_detail(None, songs[0]["mid"])  # type: ignore[arg-type]
        if not detail or not detail["media_mid"]:
            pytest.skip("没拿到 media_mid")
        url = await qqapi.get_play_url(  # type: ignore[arg-type]
            None, detail["song_mid"], detail["media_mid"]
        )
        if not url:
            pytest.skip("取不到播放链接（该曲付费/无版权/限流）")
        if not isinstance(url, str):
            # 加密容器返回解密 Task[Path]，形见 test_encrypted_container_returns_decrypt_task
            pytest.skip("本次拿到的是加密容器（返回解密任务而非直链）")
        assert url.startswith("http")
        assert any(ext in url for ext in (".mgg", ".mp3", ".m4a", ".flac", ".mp4"))

    @pytest.mark.asyncio
    async def test_encrypted_container_returns_decrypt_task(self, qqapi) -> None:
        """加密容器必须解密成本地明文文件再发（兰亭序=449201 回归）。

        匿名档常是 640k OGG **加密**容器（O8M1….mgg）；直接发 URL，
        NapCat 语音转换必炸（retcode=1200「语音转换失败」）——密文不是
        可播放音频。修复后 get_play_url 对带 ekey 的结果返回
        Task[Path]（下载+解密后的本地文件），解密产物应以 ``OggS`` 魔数开头。
        """
        detail = await qqapi.get_song_detail(None, "449201")  # type: ignore[arg-type]
        if not detail or not detail["media_mid"]:
            pytest.skip("曲库反查没拿到 media_mid")
        got = await qqapi.get_play_url(None, detail["song_mid"], detail["media_mid"])  # type: ignore[arg-type]
        if not got:
            pytest.skip("取不到播放链接（限流/风控）")
        if isinstance(got, str):
            pytest.skip("本次拿到的是明文直链，未覆盖解密分支")
        path = await asyncio.wait_for(got, timeout=120)
        assert path.exists(), "解密文件不存在"
        assert path.suffix == ".ogg"
        assert path.read_bytes()[:4] == b"OggS", f"解密后不是 OGG 明文: {path.read_bytes()[:8]!r}"


class TestEkey:
    @pytest.mark.asyncio
    async def test_ekey_and_key_derivation(self, qqapi) -> None:
        """加密容器应带回 ekey，且能派生出 512 字节音频解密密钥。

        ⚠️ 明文容器（``M800….mp3`` 等）本来就不返回 ekey，走直通不加密，
        所以只有当实际拿到的档位是加密容器时才断言 ekey 存在。
        """
        if qqapi.protocol is None:
            pytest.skip("增强引擎未安装，走内置实现")
        client = qqapi._get_client()
        if client is None:
            pytest.skip("拿不到客户端")
        songs = await qqapi.search_songs("晴天", limit=1)
        if not songs:
            pytest.skip("搜索返回 0 条")
        detail = await qqapi.get_song_detail(None, songs[0]["mid"])  # type: ignore[arg-type]
        if not detail or not detail["media_mid"]:
            pytest.skip("没拿到 media_mid")
        got = await qqapi._qp_api.resolve_url_best(client, detail["song_mid"], detail["media_mid"], 320)
        if got is None:
            pytest.skip("取链失败")
        info, quality = got
        assert quality.bitrate in (320, 640, 128)
        if not quality.is_encrypted:
            pytest.skip(f"{quality.name} 是明文容器，按设计不返回 ekey")

        assert info.has_ekey, "加密容器应返回 ekey"
        key = qqapi.protocol.derive_key(info.ekey)
        assert len(key) == 512
        dec = qqapi.protocol.StreamDecryptor(key)
        assert dec.encrypted_container
