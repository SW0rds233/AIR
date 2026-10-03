# 判断前端是否需要重新构建 (供 start.bat 调用): 产物缺失, 或源码比产物新。
# 输出单个 1/0, 便于批处理用 for /f 读取。
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'src\web\dist\index.html'
if (-not (Test-Path $dist)) { '1'; exit 0 }

$built = (Get-Item $dist).LastWriteTime
$inputs = @(
  (Join-Path $root 'src\web\src'),
  (Join-Path $root 'src\web\index.html'),
  (Join-Path $root 'src\web\vite.config.ts')
)
$newer = Get-ChildItem -Recurse -File -Path $inputs |
  Where-Object { $_.LastWriteTime -gt $built } |
  Measure-Object |
  Select-Object -ExpandProperty Count
if ($newer -gt 0) { '1' } else { '0' }
