@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先把 Python 加入 PATH。
  pause
  exit /b 1
)

echo 正在更新项目依赖...
python -m pip install --upgrade pip
if errorlevel 1 (
  echo.
  echo pip 升级失败。
  pause
  exit /b 1
)

python -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo.
  echo 依赖更新失败。
  pause
  exit /b 1
)

echo.
echo 依赖更新完成。
pause

endlocal
