# ============================================================
# 智能运维 Agent - 生产级 Dockerfile
# 多阶段构建: 依赖安装 → 生产运行
# 目标: 最小化镜像体积、安全（非 root 运行）、可观测
# ============================================================

# ---- 阶段1: 依赖安装 ----
FROM python:3.11-slim AS builder

WORKDIR /build

# 系统依赖（编译所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖清单（利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- 阶段2: 生产运行 ----
FROM python:3.11-slim

# 系统依赖（运行时所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制 site-packages
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# 创建工作目录
WORKDIR /app

# 复制源码
COPY . .

# 创建非 root 用户
RUN useradd -m -u 1001 -s /bin/bash ops-agent \
    && chown -R ops-agent:ops-agent /app

# 数据目录挂载点
VOLUME ["/app/data"]

# 环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_MOCK_MODE=true \
    APP_LOG_LEVEL=INFO

# 暴露端口（Webhook 模式）
EXPOSE 8081

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/health')" || exit 1

# 切换到非 root 用户
USER ops-agent

# 入口点
ENTRYPOINT ["python", "main.py"]

# 默认启动 Webhook 模式
CMD ["--webhook", "--port", "8081"]