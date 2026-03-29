# ===================================================================
# 局域网访问诊断脚本
# ===================================================================

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  局域网访问诊断工具" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# 获取本机 IP
Write-Host "[1/6] 获取本机 IP 地址..." -ForegroundColor Yellow
$localIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*" -and $_.IPAddress -notlike "169.*"}).IPAddress
Write-Host "本机局域网 IP: $localIP" -ForegroundColor Green
Write-Host ""

# 检查端口监听状态
Write-Host "[2/6] 检查端口监听状态..." -ForegroundColor Yellow
$ports = @(8000, 3000, 80)
foreach ($port in $ports) {
    $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        $address = $listening.LocalAddress
        if ($address -eq "0.0.0.0" -or $address -eq "::") {
            Write-Host "  ✓ 端口 $port 正确监听在所有接口 ($address)" -ForegroundColor Green
        } elseif ($address -eq "127.0.0.1" -or $address -eq "::1") {
            Write-Host "  ✗ 端口 $port 仅监听 localhost ($address) - 需要修改为 0.0.0.0" -ForegroundColor Red
        }
    } else {
        Write-Host "  - 端口 $port 未监听（服务可能未启动）" -ForegroundColor Gray
    }
}
Write-Host ""

# 检查防火墙规则
Write-Host "[3/6] 检查防火墙规则..." -ForegroundColor Yellow
$firewallRules = Get-NetFirewallRule | Where-Object {$_.DisplayName -like "*Structure*" -or $_.DisplayName -like "*8000*" -or $_.DisplayName -like "*3000*"}
if ($firewallRules) {
    Write-Host "  找到以下防火墙规则:" -ForegroundColor Green
    $firewallRules | ForEach-Object {
        $port = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $_ -ErrorAction SilentlyContinue
        Write-Host "    - $($_.DisplayName): $($_.Direction), 端口 $($port.LocalPort)" -ForegroundColor Cyan
    }
} else {
    Write-Host "  ⚠ 未找到相关防火墙规则" -ForegroundColor Yellow
}
Write-Host ""

# 测试本地访问
Write-Host "[4/6] 测试本地访问..." -ForegroundColor Yellow
$endpoints = @(
    @{Port=8000; Path="/api/health"; Name="API"},
    @{Port=3000; Path="/"; Name="前端"}
)

foreach ($endpoint in $endpoints) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$($endpoint.Port)$($endpoint.Path)" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        Write-Host "  ✓ $($endpoint.Name) (localhost:$($endpoint.Port)) 可访问" -ForegroundColor Green
    } catch {
        Write-Host "  ✗ $($endpoint.Name) (localhost:$($endpoint.Port)) 无法访问" -ForegroundColor Red
    }
}
Write-Host ""

# 检查 Docker 容器状态
Write-Host "[5/6] 检查 Docker 容器..." -ForegroundColor Yellow
try {
    $containers = docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>$null
    if ($containers) {
        Write-Host $containers -ForegroundColor Cyan
    } else {
        Write-Host "  未检测到运行中的 Docker 容器" -ForegroundColor Gray
    }
} catch {
    Write-Host "  Docker 未安装或未运行" -ForegroundColor Gray
}
Write-Host ""

# 提供修复建议
Write-Host "[6/6] 修复建议" -ForegroundColor Yellow
Write-Host ""

$needsFix = $false

# 检查是否需要配置防火墙
if (-not $firewallRules) {
    $needsFix = $true
    Write-Host "建议 1: 配置防火墙规则" -ForegroundColor Cyan
    Write-Host "运行以下命令（需要管理员权限）:" -ForegroundColor White
    Write-Host ""
    Write-Host "New-NetFirewallRule -DisplayName 'Structure API' -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow" -ForegroundColor Green
    Write-Host "New-NetFirewallRule -DisplayName 'Structure Frontend' -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow" -ForegroundColor Green
    Write-Host "New-NetFirewallRule -DisplayName 'Structure Nginx' -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow" -ForegroundColor Green
    Write-Host ""
}

# 检查服务监听地址
$localhostOnly = Get-NetTCPConnection -LocalAddress "127.0.0.1" -State Listen -ErrorAction SilentlyContinue | Where-Object {$_.LocalPort -in @(8000, 3000, 80)}
if ($localhostOnly) {
    $needsFix = $true
    Write-Host "建议 2: 修改服务监听地址" -ForegroundColor Cyan
    Write-Host "以下端口仅监听 localhost，需要修改为 0.0.0.0:" -ForegroundColor White
    $localhostOnly | ForEach-Object {
        Write-Host "  - 端口 $($_.LocalPort)" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "检查以下文件:" -ForegroundColor White
    Write-Host "  - src/structure/api_cli.py (确保 host='0.0.0.0')" -ForegroundColor Gray
    Write-Host "  - frontend/vite.config.ts (确保 host: '0.0.0.0')" -ForegroundColor Gray
    Write-Host ""
}

if (-not $needsFix) {
    Write-Host "✓ 配置看起来正确！" -ForegroundColor Green
    Write-Host ""
    Write-Host "从局域网访问:" -ForegroundColor Cyan
    Write-Host "  前端: http://$localIP:3000" -ForegroundColor White
    Write-Host "  API:  http://$localIP:8000/api" -ForegroundColor White
}

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "详细文档: NETWORK_ACCESS_GUIDE.md" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
