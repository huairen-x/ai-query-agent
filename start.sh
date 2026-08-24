#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# ============================================
#   智能问数系统 - AI Query Agent (Linux)
# ============================================

echo "============================================"
echo "  智能问数系统启动中..."
echo "  AI Query Agent - NL2SQL System"
echo "============================================"
echo ""

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "[错误] 未检测到 python3，请安装 Python 3.8+"
    exit 1
fi
echo "[✓] Python: $(python3 --version)"

# 检测模式
MOCK_MODE="${MOCK_MODE:-true}"
if [[ "$MOCK_MODE" == "true" ]]; then
    echo "[模式] Mock 模拟模式（无需 Hive 环境）"
    CONFIG_FILE="opencode.mock.json"
    MCP_SCRIPT="mcp-servers/mock_mcp_server.py"
else
    echo "[模式] 生产模式（需要 Hive 环境）"
    CONFIG_FILE="opencode.json"
    MCP_SCRIPT="mcp-servers/hive_mcp_server.py"
    # 检查必要环境变量
    for var in HIVE_HOST HIVE_PORT HIVE_USER HIVE_DATABASE; do
        if [[ -z "${!var:-}" ]]; then
            echo "[错误] 缺少环境变量: $var"
            exit 1
        fi
    done
fi

echo "[配置] $CONFIG_FILE"
echo "[MCP]  $MCP_SCRIPT"
echo ""

# 验证 MCP Server 可启动
echo "[验证] 测试 MCP Server stdio 握手..."
INIT_REQ='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"startup-check","version":"1.0"}}}'
RESPONSE=$(echo "$INIT_REQ" | timeout 5 python3 "$MCP_SCRIPT" 2>/dev/null || true)

if echo "$RESPONSE" | grep -q '"protocolVersion"'; then
    echo "[✓] MCP Server 握手成功"
else
    echo "[✗] MCP Server 启动失败"
    echo "响应: $RESPONSE"
    exit 1
fi

echo ""
echo "============================================"
echo "  系统就绪！"
echo "  MCP Server: $MCP_SCRIPT"
echo "  配置文件:   $CONFIG_FILE"
echo "============================================"
echo ""
echo "提示: 此服务为 stdio 模式，由客户端(Trae/opencode)直接调用"
echo "      如需后台运行，请使用客户端工具或 systemd 托管"
