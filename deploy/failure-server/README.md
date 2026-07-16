# parser 失败链接接收服务

接收 nonebot-plugin-parser 上报的解析失败链接，SQLite 存储，简单 HTML 查看。

## 部署（已在 al 服务器部署）

```bash
# 在服务器上
cd /opt/parser-failure-server
# 生成 API key（≥32 字节）
echo "API_KEY=$(openssl rand -hex 32)" > .env && chmod 600 .env
# 国内拉不动 docker.io 时先拉镜像源
docker pull docker.m.daocloud.io/library/python:3.12-slim
docker tag docker.m.daocloud.io/library/python:3.12-slim python:3.12-slim
# 构建启动
docker compose up -d --build
```

## nginx 反代配置（1Panel/openresty）

在 1Panel 网站管理添加反向代理，或手动加 openresty conf：

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
PARSER_FAILURE_REPORT_KEY=c87751007041581a8af57f011731d01729d67e4a62c06b0546270e77fad50a6e
```

## 端点

| 端点 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/api/report` | POST | Bearer key | 上报失败记录 |
| `/api/failures` | GET | Bearer key | 查询失败列表（分页） |
| `/` | GET | Bearer key | HTML 查看页 |
| `/health` | GET | 无 | 健康检查 |

## 安全

- 仅监听 `127.0.0.1:8317`，不对外暴露
- Bearer API key 鉴权（所有业务端点），常量时间比较防时序攻击
- key 从环境变量读，≥32 字节，不落盘代码
- pydantic 输入校验 + 字段长度上限
- 速率限制 10次/分钟/IP
- 日志脱敏：只记 url_hash（sha256[:16]）+ platform，不记完整 url
- 90 天数据自动清理

## 运维

```bash
# 查看日志
docker logs -f parser-failure-server
# 重启
docker compose -f /opt/parser-failure-server/docker-compose.yml restart
# 查看 key
cat /opt/parser-failure-server/.env
# 备份数据
cp /opt/parser-failure-server/data/failures.db /backup/
```
