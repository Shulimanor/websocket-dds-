@echo off
chcp 65001 >nul
REM =============================================
REM  WebSocket / OpenDDS 调试工具 —— 打包脚本
REM  产物: dist\WebSocketDDSTool.exe (单文件)
REM =============================================
cd /d "%~dp0"

echo [1/3] 清理旧产物...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo [2/3] PyInstaller 打包 (排除 DDS)...
D:\miniconda\python.exe -m PyInstaller ^
  --onefile ^
  --name WebSocketDDSTool ^
  --add-data "index.html;." ^
  --exclude-module cyclonedds ^
  --exclude-module numpy ^
  --exclude-module pycryptodome ^
  --noconfirm ^
  run.py

echo [3/3] 完成! 产物位于 dist\WebSocketDDSTool.exe
pause
