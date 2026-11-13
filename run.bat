@echo off
rem usage: run.bat [model.gguf] [project-folder]
rem   model.gguf is the coder; leave it out to start empty and pick models in the app's Model tab.
rem   Reasoning models (gemma-4-12b-it-Q4_K_M.gguf / Qwen3-8B-Q6_K.gguf) are looked for in E:\AI\Models.
rem   Optional third argument: SearXNG base URL (overrides SEARXNG_URL).
cd /d "%~dp0"
if not exist .venv (python -m venv .venv && .venv\Scripts\pip install -r requirements.txt)
set R=%~2
if "%R%"=="" set R=.
if not "%~3"=="" set "SEARXNG_URL=%~3"
if "%~1"=="" (
  .venv\Scripts\python server.py --root "%R%" --open
) else (
  .venv\Scripts\python server.py --model "%~1" --root "%R%" --open
)
