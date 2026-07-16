"""验证 failure_store：去重、上限淘汰、load 容错。

通过 reload 模块 + 替换 _FAILURES_PATH 指向 tmp_path，隔离真实 data_dir。
"""

import json


def _fresh_failure_store(tmp_path):
    """重新加载 failure_store，把 _FAILURES_PATH 指向 tmp_path 下的临时文件。"""
    import importlib

    import nonebot_plugin_parser.failure_store as fs

    importlib.reload(fs)
    fs._FAILURES_PATH = tmp_path / "parse_failures.json"
    fs._failures = fs._load_or_initialize()
    return fs


def test_record_new_failure(tmp_path):
    """新增记录：包含全部字段"""
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("https://bbs.nga.cn/read.php?tid=1", "nga", "ParseException: boom")

    records = fs.get_failures()
    assert len(records) == 1
    r = records[0]
    assert r["url"] == "https://bbs.nga.cn/read.php?tid=1"
    assert r["platform"] == "nga"
    assert r["error"] == "ParseException: boom"
    assert r["count"] == 1
    assert r["first_seen"] == r["last_seen"]


def test_dedup_same_url_increments_count(tmp_path):
    """同 URL 重复失败：count 递增，不新增条目"""
    fs = _fresh_failure_store(tmp_path)
    url = "https://b23.tv/abc"
    fs.record_failure(url, "bilibili", "err1")
    fs.record_failure(url, "bilibili", "err2")
    fs.record_failure(url, "bilibili", "err3")

    records = fs.get_failures()
    assert len(records) == 1
    assert records[0]["count"] == 3
    assert records[0]["error"] == "err3"  # 更新为最新


def test_eviction_when_over_limit(tmp_path):
    """超过 MAX_FAILURES 时淘汰 last_seen 最旧的"""
    fs = _fresh_failure_store(tmp_path)
    # 设小上限便于测试
    fs.MAX_FAILURES = 3
    fs.record_failure("url1", "p1", "e1")
    # 手动让 url1 的 last_seen 更早，确保它被淘汰
    fs._failures["url1"]["last_seen"] = 100
    fs.record_failure("url2", "p2", "e2")
    fs.record_failure("url3", "p3", "e3")
    fs.record_failure("url4", "p4", "e4")  # 触发淘汰

    records = fs.get_failures()
    urls = {r["url"] for r in records}
    assert len(records) == 3
    assert "url1" not in urls, "最旧的 url1 应被淘汰"
    assert {"url2", "url3", "url4"} == urls


def test_persist_and_reload(tmp_path):
    """写盘后 reload 能恢复记录"""
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("https://x.com/1", "twitter", "err")

    # 重新加载（模拟重启）
    fs2 = _fresh_failure_store(tmp_path)
    records = fs2.get_failures()
    assert len(records) == 1
    assert records[0]["url"] == "https://x.com/1"


def test_load_corrupt_json_resets(tmp_path):
    """损坏的 JSON 文件 → 重置为空，不崩"""
    bad_file = tmp_path / "parse_failures.json"
    bad_file.write_text("{broken json", encoding="utf-8")

    fs = _fresh_failure_store(tmp_path)
    assert fs.get_failures() == []


def test_clear_failures(tmp_path):
    """清空记录"""
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("u1", "p1", "e1")
    fs.record_failure("u2", "p2", "e2")
    assert len(fs.get_failures()) == 2

    fs.clear_failures()
    assert fs.get_failures() == []
    # 文件也应是空 dict
    assert json.loads(fs._FAILURES_PATH.read_text(encoding="utf-8")) == {}


def test_get_failures_sorted_by_last_seen_desc(tmp_path):
    """get_failures 按 last_seen 倒序（最新在前）"""
    fs = _fresh_failure_store(tmp_path)
    fs.record_failure("old", "p1", "e1")
    fs._failures["old"]["last_seen"] = 100
    fs._save()
    fs.record_failure("new", "p2", "e2")  # last_seen = now

    records = fs.get_failures()
    assert records[0]["url"] == "new"
    assert records[1]["url"] == "old"
