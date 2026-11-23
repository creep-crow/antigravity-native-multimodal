<#
.SYNOPSIS
    Install the native-multimodal Antigravity plugin on Windows.
.DESCRIPTION
    Copies plugin files into $env:USERPROFILE\.gemini\config\plugins\native-multimodal
    and verifies Python + FFmpeg availability.
    Source repo: https://github.com/creep-crow/antigravity-native-multimodal
#>

[CmdletBinding()]
param(
    [string]$TargetDir = $(if ($env:AGY_PLUGIN_INSTALL_DIR) { $env:AGY_PLUGIN_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".gemini\config\plugins\native-multimodal" })
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "Installing native-multimodal plugin..."
Write-Host "  Source: $RepoRoot"
Write-Host "  Target: $TargetDir"

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

foreach ($file in @("plugin.json", "hooks.json", "README.md", "README.zh-CN.md", "LICENSE", "CHANGELOG.md")) {
    $srcFile = Join-Path $RepoRoot $file
    if (Test-Path $srcFile) {
        Copy-Item -Path $srcFile -Destination (Join-Path $TargetDir $file) -Force
    }
}

foreach ($dir in @("assets", "hooks", "rules", "skills")) {
    $srcDir = Join-Path $RepoRoot $dir
    $dstDir = Join-Path $TargetDir $dir
    if (Test-Path $srcDir) {
        if (Test-Path $dstDir) {
            Remove-Item -Path $dstDir -Recurse -Force
        }
        Copy-Item -Path $srcDir -Destination $dstDir -Recurse -Force
    }
}

Get-ChildItem -Path $TargetDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

Write-Host ""
Write-Host "Environment checks:"
$pyCmd = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $pyCmd) { $pyCmd = Get-Command python -ErrorAction SilentlyContinue }
if ($pyCmd) {
    Write-Host "  [OK] python:  $($pyCmd.Source)"
} else {
    Write-Host "  [WARN] python3/python not found on PATH. Install Python 3.10+ for runtime hooks and media helper."
}

$ffCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
$fpCmd = Get-Command ffprobe -ErrorAction SilentlyContinue
if ($ffCmd -and $fpCmd) {
    Write-Host "  [OK] ffmpeg:  $($ffCmd.Source)"
    Write-Host "  [OK] ffprobe: $($fpCmd.Source)"
} else {
    Write-Host "  [WARN] ffmpeg/ffprobe not found on PATH (or set AGY_FFMPEG_DIR). Auto-bridging exotic containers and slicing require FFmpeg."
}

Write-Host ""
Write-Host "Installation complete! Enable or verify in Antigravity Customizations UI (native-multimodal)."
