#!/bin/bash
# ============================================================
# 智能运维 Agent - Docker 构建脚本 (Linux/macOS)
# 用法: ./build.sh [tag]
#       默认 tag: ops-agent:latest
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TAG="${1:-ops-agent:latest}"

echo "============================================"
echo "  智能运维 Agent - Docker 构建"
echo "  镜像: ${TAG}"
echo "============================================"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "错误: Docker 未安装。请先安装 Docker:"
    echo "  https://docs.docker.com/engine/install/"
    exit 1
fi

# 构建镜像
echo ""
echo ">>> 构建中..."
docker build \
    --platform linux/amd64 \
    -t "${TAG}" \
    -f Dockerfile \
    .

echo ""
echo ">>> 构建完成!"

# 镜像信息
IMAGE_SIZE=$(docker image inspect "${TAG}" --format='{{.Size}}' | numfmt --to=iec)
echo "  镜像: ${TAG}"
echo "  大小: ${IMAGE_SIZE}"

# 验证
echo ""
echo ">>> 验证..."
docker run --rm "${TAG}" --help 2>&1 || true

echo ""
echo "============================================"
echo "  构建成功!"
echo "============================================"
echo ""
echo "  运行方式:"
echo "    # Webhook 模式（默认）"
echo "    docker run -d -p 8081:8081 \\"
echo "      -e APP_LLM_API_KEY=sk-xxx \\"
echo "      -e APP_MOCK_MODE=false \\"
echo "      -v ops-agent-data:/app/data \\"
echo "      ${TAG}"
echo ""
echo "    # MCP Server 模式（通过 stdin/stdout）"
echo "    docker run -i --rm \\"
echo "      -e APP_MOCK_MODE=true \\"
echo "      ${TAG} --mcp"
echo ""
echo "    # 模拟测试"
echo "    docker run --rm ${TAG} --simulate all"
echo "============================================"