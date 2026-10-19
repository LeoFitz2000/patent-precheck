@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0打开材料入口.ps1"
