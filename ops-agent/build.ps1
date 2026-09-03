<#
.SYNOPSIS
    智能运维 Agent - Docker 构建脚本 (Windows PowerShell)
.DESCRIPTION
    构建 Docker 镜像并推送到指定仓库
.PARAMETER Tag
    镜像标签，默认 ops-agent:latest
.PARAMETER Push
    构建完成后推送到远程仓库
.EXAMPLE
    .\build.ps1
    .\build.ps1 -Tag "registry.example.com/ops-agent:v1.0"
    .\build.ps1 -Push
#>

param(
    [string]$Tag = "ops-agent:latest",
    [switch]$Push = $false
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  智能运维 Agent - Docker 构建" -ForegroundColor Cyan
Write-Host "  镜像: $Tag" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# 检查 Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "错误: Docker 未安装。请先安装 Docker Desktop:" -ForegroundColor Red
    Write-Host "  https://docs.docker.com/desktop/install/windows-install/" -ForegroundColor Red
    exit 1
}

# 检查 Docker 运行状态
try {
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 守护进程未运行"
    }
} catch {
    Write-Host "错误: Docker 守护进程未运行。请启动 Docker Desktop。" -ForegroundColor Red
    exit 1
}

Write-Host "`n>>> 构建中..." -ForegroundColor Yellow
docker build `
    --platform linux/amd64 `
    -t $Tag `
    -f Dockerfile `
    .

if ($LASTEXITCODE -ne 0) {
    Write-Host "错误: Docker 构建失败" -ForegroundColor Red
    exit 1
}

Write-Host "`n>>> 构建完成!" -ForegroundColor Green

# 镜像信息
$imageInfo = docker image inspect $Tag --format='{{.Size}}' | ForEach-Object { [math]::Round($_ / 1MB, 1) }
Write-Host "  镜像: $Tag" -ForegroundColor Green
Write-Host "  大小: ${imageInfo}MB" -ForegroundColor Green

# 可选推送
if ($Push) {
    Write-Host "`n>>> 推送到远程仓库..." -ForegroundColor Yellow
    docker push $Tag
    if ($LASTEXITCODE -eq 0) {
        Write-Host "推送成功: $Tag" -ForegroundColor Green
    }
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  构建成功!" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  运行方式:" -ForegroundColor White
Write-Host "    # Webhook 模式（默认）" -ForegroundColor Gray
Write-Host "    docker run -d -p 8081:8081 \`" -ForegroundColor Gray
Write-Host "      -e APP_LLM_API_KEY=sk-xxx \`" -ForegroundColor Gray
Write-Host "      -e APP_MOCK_MODE=false \`" -ForegroundColor Gray
Write-Host "      -v ops-agent-data:/app/data \`" -ForegroundColor Gray
Write-Host "      $Tag" -ForegroundColor Gray
Write-Host ""
Write-Host "    # MCP Server 模式（通过 stdin/stdout）" -ForegroundColor Gray
Write-Host "    docker run -i --rm \`" -ForegroundColor Gray
Write-Host "      -e APP_MOCK_MODE=true \`" -ForegroundColor Gray
Write-Host "      $Tag --mcp" -ForegroundColor Gray
Write-Host ""
Write-Host "    # 模拟测试" -ForegroundColor Gray
Write-Host "    docker run --rm $Tag --simulate all" -ForegroundColor Gray