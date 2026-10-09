param([string]$Python = (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'), [string]$OutputDir = (Join-Path $PSScriptRoot 'dist'))
& (Join-Path $PSScriptRoot 'build-public.ps1') -Python $Python -OutputDir $OutputDir
