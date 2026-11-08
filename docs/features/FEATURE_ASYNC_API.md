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
  "platform": "douyin", "source": "api", "error_kind": null, "error": null,
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

失败时 `error_kind` 为归一化文案：`timeout`（解析超时）/ `quota`（媒体转存配额超限）/ `cancelled`（已取消）/ `interrupted`（服务重启中断）/ `tip`（语义性提示原文，如"该作品已下架"）/ `send_error`（发送段失败，仅聊天来源，如协议端 ActionFailed）/ `parse_error`（解析失败）。API 调用方拿到的 `error` 是**脱敏后的固定文案**，详细异常只进服务端日志；聊天来源（见下节）的 `error` 保留异常详情供维护者按链接排查。

### 其他端点

| 端点 | 说明 |
|---|---|
| `GET /parser/api/tasks?limit=&offset=&url=` | 任务列表（分页，不含 result 大字段）；`url` 按规范化链接直查（大小写/fragment 不敏感），查不到返回空列表而非 404 |
| `DELETE /parser/api/tasks/{task_id}` | 取消并清理；queued/running 会先取消执行协程（await 真正结束），状态置 `cancelled` |
| `GET /parser/api/media/{task_id}/{filename}?exp=&sig=` | 媒体文件（无鉴权，HMAC 签名 URL；支持 Range 分片） |

## 任务历史与来源

任务表同时记录两类来源（`source` 字段）：

- `api`：经本 API 提交的解析，succeeded 时携带完整结果载荷；
- `chat`：bot 聊天里被自动解析的链接（v2.7.22 起轻量入库）——只记**状态与失败原因**，不存结果载荷（消息已直接发进群，避免每条消息做媒体转存挤占配额）。失败行的 `error_kind`（timeout/parse_error/send_error/tip/quota）+ `error`（给维护者的原因详情，含异常 repr）可用于按链接排查"这条链接为什么没解析出来"。

跨源语义：聊天 running 行**不参与** API 提交去重（否则调用方轮询到终态也拿不到结果载荷），同链接跨源会各解析一次；聊天行也**不计入** API 的队列上限（`429` 判定只数 `source=api` 行，聊天流量高峰不会顶满 API）。聊天入库总开关在 API **完整注册成功**（startup 链走通 = 清理 job 存在）后才打开——`parser_api_enabled=true` 但 token/base_url/fastapi 缺任一时 API 实际未注册，此时不写行，避免「有写入、无清理」的堆积。所有任务记录按 `PARSER_API_TASK_TTL`（默认 1 小时）过期清理，媒体文件随行删除。聊天记录入口整体吞异常，永不影响聊天解析本身。

## 跨机结果缓存（v2.7.24 起，可选）

相同链接（规范化后一致）的重复提交直接复用上次的完整解析结果——不再解析、不再下载。响应信封新增 `cache_hit: bool` 观测字段；202/Location 契约不变，复用路径的行直接 `succeeded`，客户端轮询首轮即拿到结果。

### 语义

- **键**：`pcache:v1:{cfg_fp}:{sha256(url_norm)[:32]}`——`cfg_fp` 是输出影响配置+插件版本的指纹，不同配置/版本的主机互不踩键；
- **条目**：HMAC 签名（密钥 = `PARSER_RCACHE_SHARED_SECRET` 派生，**独立于 API token**）+ zlib 压缩，载荷为 `media://` 占位符零改写复用；
- **本机命中**：从源任务目录硬链复用到新任务目录（秒回）；**跨机命中**：按 `PARSER_RCACHE_PEER_MAP` 自签对端媒体 URL 拉回本机（整体 10s deadline，超时按未命中走原解析），成功后回写条目为本机（二次命中不再拉取）；
- **信任边界**：条目按半可信输入处理——受限解压（zlib 炸弹截断）→ 验签 → schema 强校验（文件名白名单/任务 ID 格式/绑定键/新鲜度），任何违规按未命中；对端 URL 只来自本机配置，条目永不提供 URL 来源；
- **降级**：Redis 不可用熔断 30s、payload 超 1MB、降级产物（媒体项转存失败）、短时效内容（`PARSER_RCACHE_SKIP_KEYWORDS`，默认直播）一律不缓存/按未命中——**缓存永不影响解析主流程**；
- **cache_hit 行保留期从复用时刻起算**，条目 TTL 不超过媒体任务保留期（条目先于其引用的媒体过期）。

### 配置（详见 deploy/rcache-redis/README.md）

`PARSER_RCACHE_ENABLED` / `PARSER_RCACHE_REDIS_URL` / `PARSER_RCACHE_INSTANCE_ID`（跨机必须唯一）/ `PARSER_RCACHE_SHARED_SECRET`（跨机一致，独立于 API token）/ `PARSER_RCACHE_PEER_MAP`（其它实例 → 其 API 地址）/ `PARSER_RCACHE_TTL`（默认 86400，生效值 ≤ task_ttl−600）/ `PARSER_RCACHE_SKIP_KEYWORDS`。

部署前提：各机 NTP 时钟同步（自签 URL 有 300s 时钟余量）；Redis 仅内网监听。聊天链路缓存（还原 `ParseResult` 走渲染管线）为二期。

## 技术实现

新增包 `src/nonebot_plugin_parser/api/`：

| 模块 | 职责 |
|---|---|
| `store.py` | SQLite 任务存储（stdlib `sqlite3`，WAL，**每操作新建连接** + `asyncio.to_thread`，连接显式 close；`busy_timeout` 先于 `journal_mode` 设置、迁移容忍 duplicate column、锁竞争整体重试——并发初始化健壮性见测试）。`result_json` 存的是**占位符载荷**：本地媒体一律 `media://` 占位符，零签名、零 base_url、零宿主路径（源链接与远程媒体 URL 本身公开，原样保留） |
| `media.py` | 媒体转存（硬链接优先/复制回退）+ 进程内配额计数器（预留-提交-回滚，重启从磁盘重建） |
| `serialize.py` | `ParseResult` → 载荷：metadata 遍历 + `render_messages` → `UniMessage.export(adapter=onebot11)` → 段 JSON 化；`file://` 转存、`base64://` 先预留配额再解码落盘（魔数推断扩展名）、`http(s)://` 原样保留；合并转发 `node.data.content` 嵌套段递归改写 |
| `runner.py` | 任务执行引擎：复用 `matchers.find_parser_for_url`（共享运行态 parser 凭据/连接池）+ `parse_with_retry`（顶层超时+指数退避）。提交判定顺序：**去重（规范化 URL）→ 零抓取校验（scheme 白名单 + parser 匹配，SSRF 边界）→ 队列上限 429** |
| `chat.py` | 聊天来源任务记录助手（v2.7.22）：`record_chat_start` / `record_chat_result`，整体吞异常，永不影响聊天链路 |
| `routes.py` | FastAPI 路由（懒导入，未装 extras 零影响）：Bearer 常量时间比较 + 鉴权失败按 IP 滑窗限流（**先查桶再比对**）；GET 时把占位符现签成完整 URL（改 `base_url` 不失效存量任务，签名每次刷新）；三层穿越防护（task_id 正则 → filename 白名单正则 → DB staged 清单反查 + resolve 包含性兜底）；DELETE 取消后**重读行**再收尾（cancel 等待期间任务自行完成时走终态清理分支，杜绝「行 succeeded + 媒体 404」错配） |
| `__init__.py` | startup 固定顺序：配置校验 → DB 初始化 → **stale 标记先于清理 job 首跑**（interrupted 行先拿到 finished_at，不会被清理误删成 404）→ 配额计数器磁盘重建 → 单进程软检测（meta 表 pid，不一致仅 warning）→ 清理 job 注册 → `include_router`（注册链整体 try/except 故障隔离：可选功能失败只降级 API 自身，绝不炸 bot 启动）→ 打开聊天入库开关。清理 job（10 分钟）：TTL 过期行（queued/running 超时行同步 cancel）+ 无主媒体目录（mtime 10 分钟宽限）+ 限流桶瘦身 |

配置项（`config.py`，字段+property 双写）：`parser_api_enabled` / `parser_api_token` / `parser_api_base_url` / `parser_api_max_concurrent`(3) / `parser_api_task_ttl`(3600s) / `parser_api_media_quota_mb`(2048) / `parser_api_media_sig_ttl`(3600s)。

媒体 URL 签名：`sig_key = HMAC-SHA256(token, "media-sig-v1")` 启动派生（与鉴权密钥职责分离），`sig = HMAC(sig_key, "{task_id}/{filename}:{exp}")` 截断 hex，校验用 `hmac.compare_digest`；token 轮换即全部已签发 URL 失效。

## 测试

`tests/others/test_api_tasks.py`（37 用例，纯单元）：鉴权 401/403/限流（被限 IP 不消耗 token 比对；非 ASCII token bytes 比较不 500 不绕限流）、422 双路径、判定顺序（队列满时同 URL 仍拿回既有 id）、URL 去重规范化、202 信封 + Location/Retry-After、成功信封字段全集快照（含 cache_hit）、**落库无泄露断言**（无 base_url/sig=/file:///宿主路径）、重复 GET 现签刷新、四类段导出形态 + base64 魔数落盘、metadata 媒体改写、路径穿越全组、签名 有效/过期 410/篡改 403、扩展名白名单 `.bin` 兜底、nosniff + Range 206、DELETE running 取消语义 + DELETE→POST 不交错、配额全生命周期（预留回滚/删除释放/磁盘重建）、TTL 清理 + stale 先于清理、shutdown 取消、store 并发写、set_result 条件写（DELETE 端竞态）、错误脱敏、聊天任务生命周期与失败原因、记录入口永不抛出、`?url=` 过滤、跨源去重隔离、v2.7.21 旧库迁移、**4 线程并发 `_init_db`**（xdist 多 worker 冷启动场景：locked/duplicate column 均须被吸收）。

`tests/others/test_rcache.py`（28 用例，内存 Redis 桩）：条目往返保真（R/Z 形态）、**验签绑定键**（条目复制他键即死）、created_at 续命防护、恶意矩阵（zlib 炸弹/路径穿越文件名/v2 版本/sig 错/cfg_fp 异/JSON 损坏/staged 501 项——删键与不删键分类断言）、熔断 30s + 池满≠熔断、本机硬链复用 + 源缺失删键自愈、跨机拉取（mock 流式：落地 + CAS 回写 + 二次命中本机）、404 整体回滚、deadline 超时回落、EXDEV copy 回退、配额不足回滚、并发双提交互不破坏、exp_span 地板/封顶、TTL 对齐断言、心跳重启不误报 + 异机告警、配置校验告警、local-only nonce 派生 + 伪造条目验签失败。

另有两个跨切面防线：

- `tests/others/test_startup_smoke.py` —— **启动生命周期冒烟测试**：conftest 以环境变量让 API 全开，nonebug 会话级 lifespan 在每个测试会话 setup 阶段跑真实 startup→shutdown；本文件断言注册链的功能性结果（cleanup job 已注册、路由 401/200、KEYWORD_PARSER_MAP 非空）——startup hook 内部 fail-safe 吞异常降级也能被测出（v2.7.21 部署翻车的直接对策）。
- `tests/others/test_send_phase.py` —— parser_handler → api.chat 的**接入断言**：真实解析写 source=chat 行并置 succeeded / 发送段失败置 failed（error_kind=send_error + 详情）/ API 关闭时不写行（防止无清理面下的无限堆积）。

```bash
uv run pytest tests/others/test_api_tasks.py tests/others/test_startup_smoke.py -q
```

## 注意事项

1. **单进程假设**：NoneBot 常规单进程部署不受影响；`--reload` / 多 worker 会让 stale 标记与配额计数互相干扰（启动时以 meta 表 pid 做软检测并 warning）。
2. **单租户**：全部端点共用一个 token，任一调用方 DELETE 会影响其他调用方；对同一任务并发轮询安全。
3. **去重仅覆盖 queued/running**：任务 succeeded 后再提交同 URL 会重新解析下载（明确语义，不是缓存层）。
4. **媒体端点无鉴权**：QQ 协议端拉取图片 URL 无法携带 header，故签名 URL 设计为免 header、带过期时间；`base_url` 若经公网隧道暴露，媒体 URL 外泄即过期前可访问，请勿外传。
5. **base_url 必须协议端可达**：Docker 网络内地址或公网域名均可，末尾 `/` 自动去除。
6. **错误只给归一化文案**：解析器异常细节（可能含路径/凭据片段）只进服务端日志。
7. API 任务的失败**不会**进入 QQ 链路的失败重试队列（failure_store 只由 matcher 层写入），两层互不污染。
8. **鉴权限流按 IP 共桶**：bot 经反向代理对外时，所有调用方在 API 看来同源 IP——单个调用方的鉴权失败会触发整个反代 IP 的 429（10 次/分钟）。多调用方场景请各自带对 token，或让调用方直连 `base_url` 内网地址绕过反代。
