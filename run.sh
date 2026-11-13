#!/usr/bin/env bash
# usage: ./run.sh [model.gguf] [project-folder] [extra server.py flags]
#   model.gguf is the coder; leave it out to start empty and pick models in the app's Model tab.
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
M="$1"; R="${2:-.}"; [ $# -gt 0 ] && shift; [ $# -gt 0 ] && shift
if [ -n "$M" ]; then exec .venv/bin/python server.py --model "$M" --root "$R" --open "$@"
else exec .venv/bin/python server.py --root "$R" --open "$@"; fi
