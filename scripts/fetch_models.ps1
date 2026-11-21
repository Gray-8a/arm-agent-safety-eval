# 拉取机械臂模型（MuJoCo Menagerie，只取 panda）。
# 用法： powershell -ExecutionPolicy Bypass -File scripts/fetch_models.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$dest = Join-Path $root "third_party"
$repo = Join-Path $dest "mujoco_menagerie"

if (Test-Path (Join-Path $repo "franka_emika_panda\panda.xml")) {
    Write-Host "模型已存在：$repo" -ForegroundColor Green
    exit 0
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
if (-not (Test-Path $repo)) {
    git clone --depth 1 --filter=blob:none --sparse https://github.com/google-deepmind/mujoco_menagerie.git $repo
}
Push-Location $repo
try {
    git sparse-checkout set franka_emika_panda
} finally {
    Pop-Location
}

$panda = Join-Path $repo "franka_emika_panda\panda.xml"
if (-not (Test-Path $panda)) { throw "拉取失败，找不到 $panda" }
Write-Host "OK: $panda" -ForegroundColor Green
