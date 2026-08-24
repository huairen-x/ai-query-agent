# Trae 连接 ai-query-agent MCP 服务 — 修复报告

> **日期**: 2026-08-24  
> **服务器**: <YOUR_SERVER_IP> (cn-guangzhou)  
> **实例**: i-7xv4mthg9rsny7e29yhp  
> **状态**: ✅ 已修复，两个 MCP Server 均已连接成功

---

## 一、最终结果

| MCP Server | URL | 状态 | 工具数 |
|-----------|-----|------|--------|
| hive-query | `http://<YOUR_SERVER_IP>:8080/mcp/hive-query` | 🟢 Connected | 5 |
| metadata | `http://<YOUR_SERVER_IP>:8080/mcp/metadata` | 🟢 Connected | 5 |

**可用工具**: `query_hive`, `validate_sql`, `list_tables`, `describe_table`, `search_columns`

### Windows Trae 最终配置 (`.trae/mcp.json`)

```json
{
  "mcpServers": {
    "hive-query": {
      "type": "sse",
      "url": "http://<YOUR_SERVER_IP>:8080/mcp/hive-query"
    },
    "metadata": {
      "type": "sse",
      "url": "http://<YOUR_SERVER_IP>:8080/mcp/metadata"
    }
  }
}
```

---

## 二、问题与修复清单

### 问题 1：URL 路径不匹配

| 项目 | 内容 |
|------|------|
| **现象** | Trae 请求 `/sse/hive`，服务端只有 `/mcp/hive-query/sse`，返回 404 |
| **根因** | Trae 配置中的 URL 路径和服务端路由不一致；server 名称 `hive` vs `hive-query` 不匹配 |
| **修复** | 在 `mcp_http_gateway.py` 中添加兼容路由和别名映射 |

```python
SERVER_ALIASES = {
    "hive": "hive-query",
    "hive-query": "hive-query",
    "metadata": "metadata",
}

@app.route("/sse/<server_name>", methods=["GET"])       # 兼容旧路径
@app.route("/sse/<server_name>/message", methods=["POST"]) # 兼容 SSE message
```

### 问题 2：Gateway 运行在 Docker 容器中

| 项目 | 内容 |
|------|------|
| **现象** | 修改宿主机文件后服务行为不变，补丁不生效 |
| **根因** | Gateway 以 Docker 容器 `ai-query-agent` 运行，宿主机文件修改不影响容器内代码 |
| **修复** | 使用 `docker cp` 注入文件 + `docker restart` 重启容器 |

```bash
docker cp mcp_http_gateway.py ai-query-agent:/app/mcp_http_gateway.py
docker restart ai-query-agent
```

### 问题 3：缺少 `serverInfo` 字段（关键阻断点）

| 项目 | 内容 |
|------|------|
| **现象** | Trae 报错 `"path": ["serverInfo"], "message": "Invalid input"` |
| **根因** | MCP 2025-03-26 规范要求 initialize 响应必须包含 `serverInfo: { name, version }`，Gateway 未返回该字段，Trae 的 zod 校验失败并断开连接 |
| **修复** | 在 Gateway 的 initialize 处理中注入 `serverInfo` |

```python
if method == "initialize" and "result" in response:
    response["result"]["protocolVersion"] = "2025-03-26"
    response["result"]["serverInfo"] = {
        "name": resolved,
        "version": "1.0.0"
    }
```

**修复前响应**:
```json
{"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
```

**修复后响应**:
```json
{
  "protocolVersion": "2025-03-26",
  "capabilities": {"tools": {}},
  "serverInfo": {"name": "hive-query", "version": "1.0.0"}
}
```

### 问题 4：不符合 MCP Streamable HTTP 2025-03-26 规范

| 项目 | 内容 |
|------|------|
| **现象** | Trae 报 `streamable fetch failed`，GET 请求返回 405 |
| **根因** | Gateway 仅支持 POST，缺少 GET（SSE 流）和 DELETE（会话终止）；缺少 `Mcp-Session-Id` 响应头；协议版本返回旧版 `2024-11-05` |
| **修复** | 重写端点支持 GET/POST/DELETE 三种方法，添加 Session 管理和协议版本升级 |

```python
@app.route("/mcp/<server_name>", methods=["GET", "POST", "DELETE"])
def streamable_http_endpoint(server_name):
    # GET → SSE stream with Mcp-Session-Id
    # POST → JSON-RPC handling with protocol upgrade
    # DELETE → Session termination
```

### 问题 5：metadata URL 误配为 localhost

| 项目 | 内容 |
|------|------|
| **现象** | metadata 持续 `streamable fetch failed`，hive-query 已连接成功 |
| **根因** | `.trae/mcp.json` 中 metadata 的 URL 为 `http://localhost:8080/mcp/metadata`，Windows 本地无 MCP 服务 |
| **修复** | 将 URL 改为 `http://<YOUR_SERVER_IP>:8080/mcp/metadata` |

---

## 三、网络层排查过程

| 阶段 | 发现 | 结论 |
|------|------|------|
| 安全组 `<CLIENT_IP>/32` | 凌晨可通，白天不通 | 疑似企业网策略时段变化 |
| 安全组 `0.0.0.0/0` | TCP 连通（Test-NetConnection=True） | 网络层正常 |
| curl.exe 测试 | 返回 400 Bad Request | PowerShell JSON 转义问题，非服务端问题 |
| 容器日志分析 | 确认真实源 IP 为 `<CLIENT_IP>` | IP 匹配 |
| 最终定位 | Trae 报错含 `serverInfo` 校验错误 | **应用层协议问题，非网络问题** |

> ⚠️ **教训**: 当 TCP 连通但应用报错时，应优先检查协议兼容性而非反复排查网络。

---

## 四、修改的文件清单

| 文件 | 修改内容 |
|------|---------|
| `mcp_http_gateway.py` | ① 添加 SERVER_ALIASES 别名映射 ② 添加 /sse/\<name\> 兼容路由 ③ 重写 /mcp/\<name\> 为 Streamable HTTP 端点（GET/POST/DELETE） ④ initialize 响应注入 serverInfo + protocolVersion 2025-03-26 + Mcp-Session-Id |
| `.trae/mcp.json` | URL 从 stdio 模式改为 SSE 模式，地址指向公网 IP |

---

## 五、当前架构

```
Windows Trae IDE
    │
    ├── hive-query ──→ http://<YOUR_SERVER_IP>:8080/mcp/hive-query
    │                        │
    └── metadata   ──→ http://<YOUR_SERVER_IP>:8080/mcp/metadata
                             │
                    ┌────────▼────────┐
                    │  Docker Container │
                    │  ai-query-agent   │
                    │                   │
                    │  mcp_http_gateway │ ← Streamable HTTP 2025-03-26
                    │       │           │
                    │  mock_mcp_server  │ ← Mock 模式（SQLite 内存库）
                    └───────────────────┘
```

---

## 六、后续建议

| 优先级 | 事项 | 说明 |
|--------|------|------|
| 🔴 高 | 收紧安全组 | 当前为 `0.0.0.0/0`，建议改回 `<CLIENT_IP>/32` 或实际出口 IP |
| 🟡 中 | 切换生产模式 | 设置 `MOCK_MODE=false` + Hive 连接参数，重启容器 |
| 🟡 中 | IP 变化应对 | 如出口 IP 频繁变动，放宽安全组 IP 段或使用 SSH 隧道 |
| 🟢 低 | 持久化补丁 | 将修改后的 `mcp_http_gateway.py` 提交到 Git / 更新 Dockerfile，避免容器重建后丢失 |
| 🟢 低 | 添加 HTTPS | 通过 Nginx 反向代理提供 TLS，提升安全性 |

---

## 七、关键命令速查

```bash
# 查看容器状态
docker ps | grep ai-query-agent

# 查看容器日志
docker logs ai-query-agent --tail 50

# 部署代码更新
docker cp mcp_http_gateway.py ai-query-agent:/app/mcp_http_gateway.py
docker restart ai-query-agent

# 验证服务端
curl -s http://127.0.0.1:8080/health
curl -s -X POST http://127.0.0.1:8080/mcp/hive-query \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# 切换生产模式
docker exec ai-query-agent sed -i 's/MOCK_MODE=true/MOCK_MODE=false/' /app/.env
docker restart ai-query-agent
```
