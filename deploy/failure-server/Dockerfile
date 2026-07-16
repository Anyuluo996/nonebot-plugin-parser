# 国内服务器构建若 docker.io 拉不动，先手动拉镜像源再 tag：
#   docker pull docker.m.daocloud.io/library/python:3.12-slim
#   docker tag docker.m.daocloud.io/library/python:3.12-slim python:3.12-slim
FROM python:3.12-slim

# 非 root 用户
RUN useradd -r -u 1000 -m -d /home/app appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# 数据目录
RUN mkdir -p /data && chown appuser:appuser /data
USER appuser

ENV DATA_DIR=/data
EXPOSE 8317

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8317/health').read()" || exit 1

# 默认监听 127.0.0.1:8317；host 网络模式下不对外暴露，由宿主 nginx 反代
CMD ["uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8317"]
