# ===================================================================
# 一键配置防火墙规则
# 需要管理员权限运行
# ===================================================================

# 检查管理员权限
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "错误: 此脚本需要管理员权限运行" -ForegroundColor Red
    Write-Host ""
    Write-Host "请右键点击 PowerShell，选择'以管理员身份运行'，然后再次执行此脚本" -ForegroundColor Yellow
    exit 1
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  配置 Structure 防火墙规则" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# 定义规则
$rules = @(
    @{Name="Structure API"; Port=8000; Description="允许访问 Structure API 服务"}
)

# 创建或更新规则
foreach ($rule in $rules) {
    Write-Host "处理规则: $($rule.Name)..." -ForegroundColor Yellow

    # 检查规则是否存在
    $existing = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue

    if ($existing) {
        Write-Host "  规则已存在，删除旧规则..." -ForegroundColor Gray
        Remove-NetFirewallRule -DisplayName $rule.Name
    }

    # 创建新规则
    try {
        New-NetFirewallRule `
            -DisplayName $rule.Name `
            -Description $rule.Description `
            -Direction Inbound `
            -LocalPort $rule.Port `
            -Protocol TCP `
            -Action Allow `
            -Profile Any `
            -Enabled True | Out-Null

        Write-Host "  ✓ 已创建防火墙规则: $($rule.Name) (端口 $($rule.Port))" -ForegroundColor Green
    } catch {
        Write-Host "  ✗ 创建规则失败: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "防火墙配置完成！" -ForegroundColor Green
Write-Host ""
Write-Host "现在可以从局域网访问服务了。" -ForegroundColor White
Write-Host ""
Write-Host "获取你的局域网 IP:" -ForegroundColor Cyan
Write-Host "  ipconfig" -ForegroundColor Gray
Write-Host ""
Write-Host "从其他设备访问:" -ForegroundColor Cyan
Write-Host "  http://<你的IP>:3000  (前端)" -ForegroundColor Gray
Write-Host "  http://<你的IP>:8000  (API)" -ForegroundColor Gray
Write-Host "==================================================" -ForegroundColor Cyan
