@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title 智能问数系统 - AI Query Agent

echo ============================================
echo   智能问数系统启动中...
echo   AI Query Agent - NL2SQL System
echo ============================================
echo.

REM 检查 Python 环境
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请安装 Python 3.8+
    pause
    exit /b 1
)

REM 检测 Mock 模式
if /i "!MOCK_MODE!"=="true" (
    echo [模式] Mock 模拟模式（无需 Hive 环境）
    echo.
    echo [启动] Mock MCP Server 就绪
    echo   模拟服务: mcp-servers/mock_mcp_server.py
    echo   使用配置: opencode.mock.json（复制为 opencode.json 即可使用）
    echo.
    echo [数据] 已加载 4 张模拟表：
    echo   - t_dw_nev_sac_sale_clue（20 条线索）
    echo   - t_dw_nev_sac_sale_achievement（39 条业绩）
    echo   - t_dim_dlr_info（10 家门店）
    echo   - t_dim_car_config（7 个车系）
    echo.
    echo [就绪] 模拟系统已就绪！
    echo   在 OpenCode 中加载 opencode.mock.json 即可开始测试
    echo.
    echo 快速开始（测试用）:
    echo   1. 直接运行: python test_mock_e2e.py
    echo   2. 或在 OpenCode 中加载: opencode.mock.json
    echo   3. 输入自然语言问题，如"各门店线索量排名"
    echo.
    echo 切换到真实模式: 设置 MOCK_MODE=false 并配置 HIVE_HOST 环境变量
    echo.
    pause >nul
    exit /b 0
)

REM 检查环境变量
echo [检查] 环境变量配置...
set MISSING_ENV=0
for %%v in (HIVE_HOST HIVE_PORT HIVE_USER HIVE_PASSWORD HIVE_DATABASE HIVE_AUTH) do (
    if "!%%v!"=="" (
        echo [警告] 环境变量 %%v 未设置，将使用默认值
    )
)

echo.
echo [启动] MCP Server 连接测试...
python -c "from impala.dbapi import connect; import os; c=connect(host=os.environ.get('HIVE_HOST','localhost'), port=int(os.environ.get('HIVE_PORT','10000'))); c.close(); print('  Hive 连接成功 ✅')" 2>nul
if %errorlevel% neq 0 (
    echo [警告] Hive 连接测试失败，请检查环境变量配置
    echo   请确保以下环境变量已正确设置：
    echo   - HIVE_HOST (默认: localhost)
    echo   - HIVE_PORT (默认: 10000)
    echo   - HIVE_USER
    echo   - HIVE_PASSWORD
    echo   - HIVE_DATABASE (默认: dndc_dw)
    echo   - HIVE_AUTH (默认: PLAIN)
    echo.
    echo 你也可以设置 MOCK_MODE=true 使用模拟模式
    echo.
)

echo.
echo [启动] MCP Server 就绪
echo   1. Hive 查询执行器  : mcp-servers/hive_mcp_server.py
echo   2. 元数据查询服务   : mcp-servers/metadata_mcp_server.py
echo.
echo [就绪] 智能问数系统已就绪！
echo   使用 OpenCode 加载本目录即可开始使用
echo   配置文件: opencode.json
echo   技能文件: skills/nl2sql.md
echo   Agent 定义: agents/
echo.
echo 快速开始:
echo   1. 在 OpenCode 中打开本目录
echo   2. 选择 Sisyphus Agent 开始对话
echo   3. 输入自然语言问题，如"上月各门店线索量排名"
echo.
echo 按任意键退出...
pause >nul