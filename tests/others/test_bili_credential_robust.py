"""B 站凭据管理器健壮性测试: 失效凭据一律降级为匿名(None), 绝不带病上岗。

2026-11-08 线上事故回归: env ck 死 → 盲载死回退 cookies 文件 → 凭据「非 None
但无效」→ parse_video 带死凭据调 get_ai_conclusion → 账号未登录 -101 炸掉
整条视频解析。正确行为: 校验不过宁可匿名, 凭据失效不阻塞解析。

check_valid 是网络调用, 全部 mock 掉(按 Credential 实例的 SESSDATA 区分生死)。

注意: nonebot_plugin_parser.* 的 import 必须放在测试函数内部,
确保 conftest 的 init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import json

import pytest


def _write_cookies(path, sessdata: str):
    path.write_text(json.dumps({"SESSDATA": sessdata, "bili_jct": "fakejct", "DedeUserID": "1"}), encoding="utf-8")


def _make_manager(tmp_path, monkeypatch, env_ck: str | None, file_sessdata: str | None):
    """构造隔离的凭据管理器: 隔离 cookies 文件 + mock check_valid(按值判死活)。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers.bilibili.credential import BilibiliCredentialManager

    cookies_file = tmp_path / "bilibili_cookies.json"
    if file_sessdata is not None:
        _write_cookies(cookies_file, file_sessdata)

    # bili_ck 是只读 property(Config 字段+property 双写), patch 底层字段
    monkeypatch.setattr(pconfig, "parser_bili_ck", env_ck)

    manager = BilibiliCredentialManager(cookies_file)

    async def fake_check_valid(self):
        # 值含 "dead" 即死; check_valid 原型是实例方法。
        # SESSDATA 值要从 get_cookies() 取 —— Credential 的构造字段是小写
        # sessdata, 大小写形态不是实例属性(直接访问 AttributeError)
        return "dead" not in (self.get_cookies().get("SESSDATA") or "")

    from bilibili_api import Credential

    monkeypatch.setattr(Credential, "check_valid", fake_check_valid)
    return manager


@pytest.mark.asyncio
async def test_env_ck_dead_file_also_dead_yields_none(tmp_path, monkeypatch):
    """env ck 死 + 回退文件也死 → None(匿名), 绝不返回死凭据(本次事故根因)。"""
    manager = _make_manager(tmp_path, monkeypatch, env_ck="SESSDATA=dead-env", file_sessdata="dead-file")
    assert await manager.get() is None


@pytest.mark.asyncio
async def test_env_ck_dead_file_alive_uses_file(tmp_path, monkeypatch):
    """env ck 死 + 回退文件活 → 用文件会话(如 blogin 后文件是新的)。"""
    manager = _make_manager(tmp_path, monkeypatch, env_ck="SESSDATA=dead-env", file_sessdata="alive-file")
    cred = await manager.get()
    assert cred is not None
    assert cred.get_cookies().get("SESSDATA") == "alive-file"


@pytest.mark.asyncio
async def test_env_ck_dead_no_file_yields_none(tmp_path, monkeypatch):
    """env ck 死 + 无回退文件 → None(无文件部署的既有形态, 匿名解析)。"""
    manager = _make_manager(tmp_path, monkeypatch, env_ck="SESSDATA=dead-env", file_sessdata=None)
    assert await manager.get() is None


@pytest.mark.asyncio
async def test_env_ck_alive_saved_to_file(tmp_path, monkeypatch):
    """env ck 活 → 采用并落盘(原子写复用既有 _save 路径)。"""
    manager = _make_manager(tmp_path, monkeypatch, env_ck="SESSDATA=alive-env", file_sessdata=None)
    cred = await manager.get()
    assert cred is not None
    assert (manager._cookies_file).exists(), "有效 env ck 应回写 cookies 文件"
