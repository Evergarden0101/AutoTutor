@echo off
REM ---------------------------------------------------------------
REM  AutoTutor - one-click Windows build
REM  Produces dist\AutoTutor.exe
REM ---------------------------------------------------------------
setlocal

REM Switch the console to UTF-8 so the Chinese progress messages below,
REM and anything the Python scripts print, render instead of crashing.
chcp 65001 >nul 2>&1

echo.
echo ==== AutoTutor Windows build ====
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo [X] 找不到 Python。请先从 https://www.python.org/downloads/ 安装 Python 3.11 或更高版本，
        echo     安装时请勾选 "Add Python to PATH"。
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] 创建虚拟环境 .venv ...
    %PY% -m venv .venv || goto :error
) else (
    echo [1/4] 已存在虚拟环境 .venv
)

echo [2/4] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul || goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
".venv\Scripts\python.exe" -m pip install pyinstaller || goto :error

echo [3/4] 运行自检 ...
".venv\Scripts\python.exe" -m pytest -q tests 2>nul
if errorlevel 1 echo     (测试未通过或未安装 pytest，继续构建)

echo [4/4] 打包 ...
".venv\Scripts\python.exe" build_exe.py --clean || goto :error

echo.
echo ==== 构建完成 ====
echo 可执行文件： %CD%\dist\AutoTutor.exe
echo.
pause
exit /b 0

:error
echo.
echo [X] 构建失败，请查看上面的错误信息。
pause
exit /b 1
