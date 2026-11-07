# 异步解析 API（提交链接 → 任务 ID → 轮询结果）

## 功能概述

在插件内挂载一组 HTTP 端点，供外部调用方（解析桥、点歌联动、脚本等）以异步任务方式调用解析管线，**不经过 QQ 消息链路**：

1. `POST /parser/api/tasks` 提交链接 → `202` 返回任务 ID；
2. `GET /parser/api/tasks/{task_id}` 轮询 → 状态机推进（`queued → running → succeeded | failed | cancelled`）；
3. 成功后拿到**双形态结果**：`metadata`（平台/标题/作者/文案/媒体清单）+ `messages`（OneBot V11 消息段数组，可直接经 NapCat 等协议端发送）；
4. 媒体文件（图片/视频/语音/渲染图切片）由签名 URL 交付：`GET /parser/api/media/{task_id}/{filename}?exp=&sig=`。

参考设计：Azure 异步请求-回复模式（202 + Location + 轮询端点 + 状态机）、仓库内 `deploy/failure-server` 的 Bearer 鉴权与限流先例。

## 使用方法

### 启用

```bash
uv add "nonebot-plugin-parser[api]"   # fastapi 经 nonebot2[fastapi] 引入
```

`.env`（三项齐备才注册路由，缺任一仅 log 不报错、其余功能零影响）：

```dotenv
PARSER_API_ENABLED=true
PARSER_API_TOKEN=<openssl rand -hex 32 生成>
PARSER_API_BASE_URL=https://bot.example.com   # QQ 协议端所在网络可达的地址
```

### 提交任务

```bash
curl -X POST https://bot.example.com/parser/api/tasks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://v.douyin.com/xxx"}'
```

`202 Accepted`，响应头带 `Location: /parser/api/tasks/{task_id}` 与 `Retry-After: 2`（轮询间隔建议 ≥ 2s）：

```json
{"task_id": "a1B2c3D4e5F6g7H8i9J0k1", "status": "queued"}
```

### 轮询结果

```bash
curl -H "Authorization: Bearer <token>" https://bot.example.com/parser/api/tasks/{task_id}
```

```json
{
  "task_id": "...", "status": "succeeded", "url": "https://v.douyin.com/xxx",
  "platform": "douyin", "error_kind": null, "error": null,
  "created_at": 1762400000, "started_at": 1762400001, "finished_at": 1762400012,
  "result": {
    "metadata": {
      "platform": "douyin", "platform_display": "抖音",
      "title": "...", "text": "...", "timestamp": 1762399900, "url": "...",
      "author": {"name": "...", "avatar": "https://…/parser/api/media/…", "description": null},
      "contents": [{"type": "video", "duration": 12.3, "cover": "https://…", "file": "https://…"}],
      "graphics": [], "repost": null
    },
    "messages": [[
      {"type": "image", "data": {"file": "https://…/parser/api/media/…"}},
      {"type": "text",  "data": {"text": "标题…"}}
    ]]
  }
}
```

失败时 `error_kind` 为归一化文案：`timeout`（解析超时）/ `quota`（媒体转存配额超限）/ `cancelled`（已取消）/ `interrupted`（服务重启中断）/ `tip`（语义性提示原文，如"该作品已下架"）/ `parse_error`（固定"解析失败"）。**详细异常只进服务端日志**，不回显给调用方。

### 其他端点

| 端点 | 说明 |
|---|---|
| `GET /parser/api/tasks?limit=&offset=` | 任务列表（分页，不含 result 大字段） |
| `DELETE /parser/api/tasks/{task_id}` | 取消并清理；queued/running 会先取消执行协程（await 真正结束），状态置 `cancelled` |
| `GET /parser/api/media/{task_id}/{filename}?exp=&sig=` | 媒体文件（无鉴权，HMAC 签名 URL；支持 Range 分片） |

## 技术实现

新增包 `src/nonebot_plugin_parser/api/`：

| 模块 | 职责 |
|---|---|
| `store.py` | SQLite 任务存储（stdlib `sqlite3`，WAL，**每操作新建连接** + `asyncio.to_thread`）。`result_json` 存的是**占位符载荷**：零 URL、零签名、零宿主路径 |
| `media.py` | 媒体转存（硬链接优先/复制回退）+ 进程内配额计数器（预留-提交-回滚，重启从磁盘重建） |
| `serialize.py` | `ParseResult` → 载荷：metadata 遍历 + `render_messages` → `UniMessage.export(adapter=onebot11)` → 段 JSON 化；`file://` 转存、`base64://` 先预留配额再解码落盘（魔数推断扩展名）、`http(s)://` 原样保留；合并转发 `node.data.content` 嵌套段递归改写 |
| `runner.py` | 任务执行引擎：复用 `matchers.find_parser_for_url`（共享运行态 parser 凭据/连接池）+ `parse_with_retry`（顶层超时+指数退避）。提交判定顺序：**去重（规范化 URL）→ 零抓取校验（scheme 白名单 + parser 匹配，SSRF 边界）→ 队列上限 429** |
| `routes.py` | FastAPI 路由（懒导入，未装 extras 零影响）：Bearer 常量时间比较 + 鉴权失败按 IP 滑窗限流（**先查桶再比对**）；GET 时把占位符现签成完整 URL（改 `base_url` 不失效存量任务，签名每次刷新）；三层穿越防护（task_id 正则 → filename 白名单正则 → DB staged 清单反查 + resolve 包含性兜底） |
| `__init__.py` | startup 固定顺序：DB 初始化 → **stale 标记先于清理 job 首跑**（interrupted 行先拿到 finished_at，不会被清理误删成 404）→ 配额计数器磁盘重建 → 单进程软检测（meta 表 pid，不一致仅 warning）→ 配置校验 → `include_router`。清理 job（10 分钟）：TTL 过期行（queued/running 超时行同步 cancel）+ 无主媒体目录（mtime 10 分钟宽限）+ 限流桶瘦身 |

配置项（`config.py`，字段+property 双写）：`parser_api_enabled` / `parser_api_token` / `parser_api_base_url` / `parser_api_max_concurrent`(3) / `parser_api_task_ttl`(3600s) / `parser_api_media_quota_mb`(2048) / `parser_api_media_sig_ttl`(3600s)。

媒体 URL 签名：`sig_key = HMAC-SHA256(token, "media-sig-v1")` 启动派生（与鉴权密钥职责分离），`sig = HMAC(sig_key, "{task_id}/{filename}:{exp}")` 截断 hex，校验用 `hmac.compare_digest`；token 轮换即全部已签发 URL 失效。

## 测试

`tests/others/test_api_tasks.py`（29 用例，纯单元）：鉴权 401/403/限流（被限 IP 不消耗 token 比对）、422 双路径、判定顺序（队列满时同 URL 仍拿回既有 id）、URL 去重规范化、202 信封 + Location/Retry-After、成功信封字段全集快照、**落库无泄露断言**（无 base_url/sig=/file:///宿主路径）、重复 GET 现签刷新、四类段导出形态 + base64 魔数落盘、metadata 媒体改写、路径穿越全组、签名 有效/过期 410/篡改 403、扩展名白名单 `.bin` 兜底、nosniff + Range 206、DELETE running 取消语义 + DELETE→POST 不交错、配额全生命周期（预留回滚/删除释放/磁盘重建）、TTL 清理 + stale 先于清理、shutdown 取消、store 并发写、set_result 条件写（DELETE 端竞态）、错误脱敏。

```bash
uv run pytest tests/others/test_api_tasks.py -q
```

## 注意事项

1. **单进程假设**：NoneBot 常规单进程部署不受影响；`--reload` / 多 worker 会让 stale 标记与配额计数互相干扰（启动时以 meta 表 pid 做软检测并 warning）。
2. **单租户**：全部端点共用一个 token，任一调用方 DELETE 会影响其他调用方；对同一任务并发轮询安全。
3. **去重仅覆盖 queued/running**：任务 succeeded 后再提交同 URL 会重新解析下载（明确语义，不是缓存层）。
4. **媒体端点无鉴权**：QQ 协议端拉取图片 URL 无法携带 header，故签名 URL 设计为免 header、带过期时间；`base_url` 若经公网隧道暴露，媒体 URL 外泄即过期前可访问，请勿外传。
5. **base_url 必须协议端可达**：Docker 网络内地址或公网域名均可，末尾 `/` 自动去除。
6. **错误只给归一化文案**：解析器异常细节（可能含路径/凭据片段）只进服务端日志。
7. API 任务的失败**不会**进入 QQ 链路的失败重试队列（failure_store 只由 matcher 层写入），两层互不污染。
