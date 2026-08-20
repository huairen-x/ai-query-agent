# 智能问数系统 - AI Query Agent

## 系统架构

```
用户提问
    │
    ▼
┌─────────────┐
│  Sisyphus   │  ← 主控 Agent（协调者）
│  (主控)     │
└──────┬──────┘
       │
       ├──→ ┌─────────────┐
       │    │  Librarian  │  ← 查询表结构、字段信息
       │    │  (元数据)    │     工具：list_tables, describe_table, search_columns
       │    └─────────────┘
       │
       ├──→ ┌─────────────┐
       │    │   Oracle    │  ← 生成 Hive SQL
       │    │  (SQL生成)   │     技能：nl2sql.md
       │    └─────────────┘
       │
       ├──→ ┌─────────────┐
       │    │  Validator  │  ← 自动审查 SQL 质量
       │    │  (审查)      │     检查语法、逻辑、性能
       │    └─────────────┘
       │
       ▼
┌─────────────┐
│  查询结果    │  ← 表格 + 自然语言解读
└─────────────┘
```

## MCP Servers

| 服务 | 文件 | 功能 |
|------|------|------|
| hive-query | `mcp-servers/hive_mcp_server.py` | 执行 Hive SQL 查询，返回 Markdown 表格 |
| metadata | `mcp-servers/metadata_mcp_server.py` | 查询表结构、字段信息、跨表搜索 |

## Agents

| Agent | 角色 | 职责 |
|-------|------|------|
| Sisyphus | 主控 | 协调整个工作流，理解需求，呈现结果 |
| Librarian | 元数据 | 查询表和字段结构信息 |
| Oracle | SQL 生成 | 根据需求生成正确的 Hive SQL |
| Validator | 审查 | 自动审查 SQL 质量，确保正确性 |

## 工作流程

1. **分析需求** → Sisyphus 理解用户问题
2. **查询元数据** → Librarian 查找相关表结构
3. **生成 SQL** → Oracle 生成 Hive SQL（含自我审查）
4. **审查 SQL** → Validator 自动审查（AI 自查，无需人工）
5. **执行查询** → Sisyphus 调用 hive-query 执行
6. **解读结果** → Sisyphus 用自然语言总结

## 快速开始

```bash
# 1. 设置环境变量
set HIVE_HOST=your_hive_host
set HIVE_PORT=10000
set HIVE_USER=your_user
set HIVE_PASSWORD=your_password
set HIVE_DATABASE=dndc_dw
set HIVE_AUTH=PLAIN

# 2. 启动
start.bat

# 3. 在 OpenCode 中打开本目录，选择 Sisyphus Agent
# 4. 输入自然语言问题，如：
#    "上月各门店的线索量排名"
#    "本月大定净量前十的门店"
#    "各区域交车完成率"
```

## 业务术语对照

| 编码 | 含义 | 说明 |
|------|------|------|
| 1001 | 大定 | 正项 |
| 1002 | 大定退订 | 逆向项 |
| 1003 | 锁单 | 正项 |
| 1004 | 锁单作废 | 逆向项 |
| 1005 | 交车 | 正项 |
| 1006 | 交车销退 | 逆向项 |

> 净量 = 正项 - 逆向项