@echo off
rem starts (or restarts) the local SearXNG that the Web button uses
cd /d "%~dp0"
docker compose up -d && echo SearXNG is up at http://127.0.0.1:8080
