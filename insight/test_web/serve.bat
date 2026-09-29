@echo off
cd /d "%~dp0"
echo LumenShop test site at http://localhost:5500
python -m http.server 5500
