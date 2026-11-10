@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 goto no_directory

set "UV_EXE=%CD%\.tools\uv.exe"
if exist "%UV_EXE%" goto start_app

echo Installing uv into the project folder...
set "UV_UNMANAGED_INSTALL=%CD%\.tools"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; Invoke-RestMethod 'https://astral.sh/uv/0.13.6/install.ps1' | Invoke-Expression"
if errorlevel 1 goto failed
if not exist "%UV_EXE%" goto failed

:start_app
set "UV_PROJECT_ENVIRONMENT=.venv"
echo Preparing the virtual environment and starting Island Inline Watcher...
"%UV_EXE%" run --locked --managed-python --python 3.12 python island_inline_watcher.py
if errorlevel 1 goto failed
popd
exit /b 0

:failed
echo.
echo Startup failed. See the error above.
popd
pause
exit /b 1

:no_directory
echo Could not open the project folder. Extract the entire ZIP and try again.
pause
exit /b 1
