$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistDir = Join-Path $env:TEMP "Clone-PyInstaller-Dist"
$WorkDir = Join-Path $env:TEMP "Clone-PyInstaller-Work"
$ModelPath = Join-Path $ProjectDir "_internal\models\Qwen3-4B-Q4_K_M.gguf"

if (-not (Test-Path -LiteralPath $ModelPath)) {
    throw "Не найдена модель: $ModelPath"
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name "Clone" `
    --distpath $DistDir `
    --workpath $WorkDir `
    --specpath $env:TEMP `
    --collect-all llama_cpp `
    --collect-all diskcache `
    --add-data "$ModelPath;models" `
    (Join-Path $ProjectDir "chat_app.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller завершился с ошибкой: $LASTEXITCODE"
}

$BuiltApp = Join-Path $DistDir "Clone"
Copy-Item -LiteralPath (Join-Path $BuiltApp "Clone.exe") -Destination (Join-Path $ProjectDir "Clone.exe") -Force
Copy-Item -Path (Join-Path $BuiltApp "_internal\*") -Destination (Join-Path $ProjectDir "_internal") -Recurse -Force
$OldExe = Join-Path $ProjectDir "Моя нейронка.exe"
if (Test-Path -LiteralPath $OldExe) {
    Remove-Item -LiteralPath $OldExe
}
Write-Host "Готово: $(Join-Path $ProjectDir 'Clone.exe')"
