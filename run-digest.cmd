@echo off
REM Daily job digest, for Windows Task Scheduler.
REM Task Scheduler starts with a bare PATH that usually has neither uv nor this directory, so the
REM repo root is derived from this file's own location and uv is looked up explicitly.
setlocal
set "REPO=%~dp0"
if "%UV%"=="" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" set "UV=uv"
cd /d "%REPO%" || exit /b 1
"%UV%" run digest.py >> "%REPO%work\digest-task.log" 2>&1
exit /b %ERRORLEVEL%
