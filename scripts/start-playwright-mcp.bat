@echo off
REM Start Playwright MCP with storage + video caps.
REM Output stays under .playwright-mcp (never the repo root).
REM Keep this window open while running langgraph dev.
cd /d "%~dp0.."
set "MEDIA_DIR=%CD%\.playwright-mcp"
if not "%INSIGHT_MEDIA_DIR%"=="" set "MEDIA_DIR=%INSIGHT_MEDIA_DIR%"
if not exist "%MEDIA_DIR%" mkdir "%MEDIA_DIR%"
echo Ensuring Playwright ffmpeg is installed (required for video)...
call npx --yes playwright install ffmpeg
echo Starting Playwright MCP on :8931
echo output-dir=%MEDIA_DIR%
npx --yes @playwright/mcp@latest --port 8931 --caps=storage,devtools --isolated --output-dir "%MEDIA_DIR%"
