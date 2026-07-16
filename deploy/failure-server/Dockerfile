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

# 只读根文件系统：仅 /data 与 /tmp 可写；只读根防止运行时篡改代码
ENV DATA_DIR=/data
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()" || exit 1

CMD ["uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8000"]
