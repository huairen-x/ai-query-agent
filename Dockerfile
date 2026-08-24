FROM python:3.11-slim

WORKDIR /app

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir flask flask-cors

# 复制项目文件
COPY . .

# 环境变量默认值
ENV MOCK_MODE=true
ENV MCP_HTTP_HOST=0.0.0.0
ENV MCP_HTTP_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "mcp_http_gateway.py"]
