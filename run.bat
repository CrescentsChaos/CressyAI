@echo off
rem usage: run.bat model.gguf [project-folder]
if "%~1"=="" (echo usage: run.bat model.gguf [project-folder] & exit /b 1)
cd /d "%~dp0"
if not exist .venv (python -m venv .venv && .venv\Scripts\pip install -r requirements.txt)
set R=%~2
if "%R%"=="" set R=.
.venv\Scripts\python server.py --model "%~1" --root "%R%" --open
