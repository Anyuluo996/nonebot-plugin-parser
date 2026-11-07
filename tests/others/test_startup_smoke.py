"""NoneBot 启动生命周期冒烟测试（API 全开）。

背景：2026-11-08 部署翻车 —— 31 个 API 单测全绿（挂在独立 FastAPI app 上），
但 cleanup job 的 ``scheduled_job`` 误传 ``replace_existing`` 只在**真实 NoneBot
startup** 时爆炸，uvicorn "Application startup failed"，容器崩溃循环。单元测试
的隔离反而成了盲区。

防线（conftest 配合）：conftest 以环境变量让 API 全开，nonebug 插件的会话级
autouse lifespan（``nonebug_init`` fixture）在每个测试会话 setup 阶段跑一遍
**真实 startup → shutdown 全周期** —— 任何 startup hook 回归都会当场炸掉整个
测试会话，而不是部署时才炸。

本文件对会话 lifespan 的结果做**功能性断言**（job / 路由确实存在）—— 连
startup hook 内部的故障隔离（fail-safe 吞异常降级）也能测出，防止「静默降级
掩盖回归」。会话 lifespan 只跑一轮（apscheduler 单例不可二次 start），故本
文件不再进入任何会再次触发 lifespan 的上下文，HTTP 直接打 ASGI transport。
"""

import httpx
import pytest


@pytest.fixture
async def smoke_client():
    """直连 driver 的 ASGI app（lifespan 已由 nonebug 会话 fixture 管理）。"""
    import nonebot

    transport = httpx.ASGITransport(app=nonebot.get_asgi())
    async with httpx.AsyncClient(transport=transport, base_url="http://startup-smoke.test") as client:
        yield client


async def test_startup_registered_api_functionally(smoke_client):
    """会话 lifespan 后，API 注册链的每一环都真实存在且可用。"""
    from nonebot_plugin_apscheduler import scheduler

    from nonebot_plugin_parser.api import routes, runner
    from nonebot_plugin_parser.config import pconfig

    # 防同会话更早的 API 用例污染限流桶 / 签名密钥缓存
    routes._rate_buckets.clear()
    routes.reset_sign_key()
    token = pconfig.api_token

    # 1. 清理 job 已注册（翻车点的直接断言；startup hook fail-safe 吞异常时此处失败）
    assert scheduler.get_job("parser-api-task-cleanup") is not None
    # 2. API 路由已挂载：无鉴权 401（路由存在且鉴权生效），未知名 404
    assert (await smoke_client.get("/parser/api/tasks")).status_code == 401
    assert (await smoke_client.get("/parser/api/definitely-not-exist")).status_code == 404
    # 3. 带 token 后任务列表端点真正可用（store / routes / 签名链路全通）
    r = await smoke_client.get("/parser/api/tasks", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"total", "limit", "offset", "items"}
    assert isinstance(body["items"], list)
    # 4. 解析 matcher 已注册（register_parser_matcher hook 生效）
    from nonebot_plugin_parser.matchers import KEYWORD_PARSER_MAP

    assert KEYWORD_PARSER_MAP, "startup 后 KEYWORD_PARSER_MAP 为空, 解析 matcher 未注册"
    # 5. 无执行协程残留（shutdown 语义可用）
    assert runner.registry == {}
