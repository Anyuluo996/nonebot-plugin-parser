# parser 失败链接接收服务

接收 nonebot-plugin-parser 上报的解析失败链接，SQLite 存储，简单 HTML 查看。

## 部署

```bash
# 在服务器上（部署目录可自定，下文以 /opt/parser-failure-server 为例）
cd /opt/parser-failure-server
# 生成 API key（≥32 字节），只写入服务器上的 .env，不要提交进任何仓库
echo "API_KEY=$(openssl rand -hex 32)" > .env && chmod 600 .env
# 国内拉不动 docker.io 时先拉镜像源
docker pull docker.m.daocloud.io/library/python:3.12-slim
docker tag docker.m.daocloud.io/library/python:3.12-slim python:3.12-slim
# 构建启动
docker compose up -d --build
```

## nginx 反代配置（示例）

手动加反代 conf（1Panel 等面板同理）：

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;  # 换成你的域名

    # ssl_certificate /path/to/cert;
    # ssl_certificate_key /path/to/key;

    location / {
        proxy_pass http://127.0.0.1:8317;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 插件配置（.env）

```
PARSER_FAILURE_REPORT_ENABLED=true
PARSER_FAILURE_REPORT_URL=https://your-domain.com
PARSER_FAILURE_REPORT_KEY=<openssl rand -hex 32 生成的值，与服务器 .env 的 API_KEY 一致>
```

## 端点

| 端点 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/api/report` | POST | 无（白名单+枚举+限流防滥用，见 server.py 文档） | 上报失败记录 |
| `/api/failures` | GET | Bearer key | 查询失败列表（分页） |
| `/` | GET | Bearer key | HTML 查看页 |
| `/health` | GET | 无 | 健康检查 |

## 安全

- 仅监听 `127.0.0.1:8317`，不对外暴露
- 读端点（`/api/failures`、`/`）Bearer API key 鉴权，常量时间比较防时序攻击
- key 从环境变量读，≥32 字节，不落盘代码、不写入公开文档
- 上报端点公开但受多重校验限制：URL 域名白名单 + platform 枚举 + 请求体/字段长度上限 + 服务端去重
- 上报速率限制 5 次/分钟/IP
- 日志脱敏：只记 url_hash（sha256[:16]）+ platform，不记完整 url
- 90 天数据自动清理（启动时执行一次，此后每小时周期执行）

## 运维

```bash
# 查看日志
docker logs -f parser-failure-server
# 重启
docker compose -f /opt/parser-failure-server/docker-compose.yml restart
# 备份数据
cp /opt/parser-failure-server/data/failures.db /backup/
```
