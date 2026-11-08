# 结果缓存 Redis 部署

为 nonebot-plugin-parser 的跨机解析结果缓存提供共享索引（键空间 `pcache:*`，
条目带 HMAC 签名与 TTL，Redis 可丢——`appendonly no`）。

## 部署

```bash
cd deploy/rcache-redis   # 上传到服务器后的目录
cp redis.conf.example redis.conf && chmod 600 redis.conf
# 编辑 redis.conf：bind 改本机内网地址；两处 <密码> 填同一个 openssl rand -hex 32 的值
docker compose up -d
# 验证
docker exec rcache-redis redis-cli -u "redis://pcache:<密码>@127.0.0.1:6379/0" ping
```

## 插件侧配置（各主机 .env）

```dotenv
PARSER_RCACHE_ENABLED=true
PARSER_RCACHE_REDIS_URL=redis://pcache:<密码>@<redis主机内网地址>:6379/0
PARSER_RCACHE_INSTANCE_ID=<本机唯一标识，如 host-a / host-b>
PARSER_RCACHE_SHARED_SECRET=<openssl rand -hex 32，各机一致，独立于 PARSER_API_TOKEN>
PARSER_RCACHE_PEER_MAP={"host-a": "http://<host-a 内网地址>:8080"}
PARSER_RCACHE_TTL=86400
```

要点：

- `PARSER_RCACHE_INSTANCE_ID` 各机唯一；`PEER_MAP` 每台配置「其它实例 → 其 API 地址」。
- `SHARED_SECRET` 必须各机一致且**独立于 API token**；轮换会使全部缓存条目失效。
- Redis 不可用时一切照旧（按未命中降级，熔断 30s），不影响解析主流程。
- 命中含媒体的结果时经对端媒体端点拉取文件（签名 URL 免 header），要求对端
  `PARSER_API_*` 正常且各机时钟经 NTP 同步。

## 聊天链路复用的部署前提（v2.7.25 起）

聊天与 API 自动共享缓存（聊天命中重放直发、解析成功种缓存），除上述配置外还需：

1. **API 栈完整注册**：`PARSER_API_ENABLED/TOKEN/BASE_URL` 三项齐备（聊天缓存依赖 API 的清理 job 兜底媒体目录），否则聊天侧自动不启用；
2. **QQ 协议端（NapCat 等）能读到 `api_media/`**：命中重放以 `file://` 引用插件数据目录（localstore data_dir）下 `nonebot_plugin_parser/api_media/` 的文件——NapCat 在独立容器部署时，须把该宿主目录**以相同容器内路径**挂载进协议端容器（与既有下载缓存目录的挂载方式一致）。挂载缺失的表现：命中后发送全部失败，插件自动清场回源解析（仅零发出时），功能退化不出错但跨机复用失效。可在协议端容器内 `ls <data_dir>/nonebot_plugin_parser/api_media/` 验证可见性。

## 验证清单

- 两台先后提交同链接：第二台 `GET /tasks/{id}` 信封 `"cache_hit": true`；
- 聊天发同链接：秒回且任务历史里聊天行 `cache_hit=1`、`staged_bytes>0`；
- 停 Redis：解析照常（日志见熔断告警），恢复后缓存恢复；
- 直播链接（`PARSER_RCACHE_SKIP_KEYWORDS` 命中 keyword）：正常解析但不种缓存。
