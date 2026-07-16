"""验证 L2(重试状态机) + L3(上报客户端)。

failure_store 扩展字段测试 + failure_reporter mock 测试。
"""

import pytest


def _fresh_failure_store(tmp_path):
    import importlib

    import nonebot_plugin_parser.failure_store as fs

    importlib.reload(fs)
    fs._FAILURES_PATH = tmp_path / "parse_failures.json"
    fs._failures = fs._load_or_initialize()
    return fs


# ── L2: failure_store 状态机 ──────────────────────────────────────


def test_record_failure_initializes_retries_reported(tmp_path):
    """新增记录含 retries=0, reported=False"""
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("u1", "nga", "err")
    rec = fs.get_failures()[0]
    assert rec["retries"] == 0
    assert rec["reported"] is False


def test_record_failure_resets_retries_on_user_retry(tmp_path):
    """同 URL 再次失败（用户重新触发）→ 重置 retries/reported"""
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("u1", "nga", "e1")
    fs.mark_retried("u1", "e2")
    fs.mark_reported("u1")
    assert fs.get_failures()[0]["retries"] == 1
    assert fs.get_failures()[0]["reported"] is True

    fs.record_failure("u1", "nga", "e3")  # 用户又触发了一次
    rec = fs.get_failures()[0]
    assert rec["retries"] == 0
    assert rec["reported"] is False
    assert rec["count"] == 2


def test_get_retryable_failures_filters(tmp_path):
    """只返回 retries<max 且 reported=False"""
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("ok", "p1", "e")  # retries=0 reported=False
    fs.record_failure("done", "p2", "e")
    fs.mark_retried("done", "e")
    fs.mark_retried("done", "e")
    fs.mark_retried("done", "e")  # retries=3
    fs.record_failure("reported", "p3", "e")
    fs.mark_reported("reported")

    pending = fs.get_retryable_failures(max_retries=3)
    urls = {r["url"] for r in pending}
    assert urls == {"ok"}
    assert "done" not in urls  # retries=3 已达上限
    assert "reported" not in urls  # 已上报


def test_mark_retried_increments(tmp_path):
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("u1", "p1", "e1")
    fs.mark_retried("u1", "e2")
    fs.mark_retried("u1", "e3")
    rec = fs.get_failures()[0]
    assert rec["retries"] == 2
    assert rec["error"] == "e3"


def test_mark_success_deletes(tmp_path):
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("u1", "p1", "e1")
    fs.mark_success("u1")
    assert fs.get_failures() == []


def test_mark_reported_persists(tmp_path):
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("u1", "p1", "e1")
    fs.mark_reported("u1")
    # reload 验证持久化
    fs2 = _fresh_failure_store(tmp_path)
    assert fs2.get_failures()[0]["reported"] is True


# ── L3: failure_reporter ──────────────────────────────────────────
# pconfig 的 failure_* 是只读 property，测试里 monkeypatch Config 类的底层字段


@pytest.fixture
def _report_enabled(monkeypatch):
    """开启上报并配置 url（上报公开无需 key）。

    pconfig 的 failure_* 是 @property 返回 self.parser_*（pydantic 字段）。
    monkeypatch 实例的 __dict__ 无效（property 优先）；改 monkeypatch property 本身。
    """
    from nonebot_plugin_parser.config import Config

    monkeypatch.setattr(Config, "failure_report_enabled", property(lambda self: True))
    monkeypatch.setattr(Config, "failure_report_url", property(lambda self: "http://localhost:9999"))


@pytest.mark.asyncio
async def test_report_disabled_returns_false(monkeypatch):
    """未启用上报 → False"""
    from nonebot_plugin_parser import failure_reporter as fr
    from nonebot_plugin_parser.config import Config

    monkeypatch.setattr(Config, "failure_report_enabled", property(lambda self: False))
    ok = await fr.report_failure_record({"url": "u1"})
    assert ok is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("_report_enabled")
async def test_report_missing_url_returns_false(monkeypatch):
    """启用但 url 缺失 → False"""
    from nonebot_plugin_parser import failure_reporter as fr
    from nonebot_plugin_parser.config import Config

    monkeypatch.setattr(Config, "failure_report_url", property(lambda self: None))
    ok = await fr.report_failure_record({"url": "u1"})
    assert ok is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("_report_enabled")
async def test_report_success_marks_reported(monkeypatch, tmp_path):
    """上报成功 → mark_reported 被调用"""
    # 重置 store 到 tmp
    import importlib

    from nonebot_plugin_parser import failure_store as fs
    from nonebot_plugin_parser import failure_reporter as fr

    importlib.reload(fs)
    fs._FAILURES_PATH = tmp_path / "parse_failures.json"
    fs._failures = fs._load_or_initialize()
    fs.record_failure("u1", "nga", "err")
    fr.mark_reported = fs.mark_reported  # 指向同一实例

    class _FakeResp:
        status_code = 200
        text = "ok"

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr(fr.httpx, "AsyncClient", _FakeClient)
    record = fs.get_failures()[0]
    ok = await fr.report_failure_record(record)
    assert ok is True
    assert fs.get_failures()[0]["reported"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("_report_enabled")
async def test_report_http_error_returns_false(monkeypatch):
    """服务端非 200 → False，不 mark_reported"""
    from nonebot_plugin_parser import failure_reporter as fr

    class _FakeResp:
        status_code = 500
        text = "server error"

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr(fr.httpx, "AsyncClient", _FakeClient)
    ok = await fr.report_failure_record({"url": "u1", "platform": "p"})
    assert ok is False
