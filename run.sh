#!/usr/bin/env bash
# usage: ./run.sh model.gguf [project-folder] [extra server.py flags]
[ -z "$1" ] && { echo "usage: $0 model.gguf [project-folder]"; exit 1; }
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
M="$1"; R="${2:-.}"; shift; [ $# -gt 0 ] && shift
exec .venv/bin/python server.py --model "$M" --root "$R" --open "$@"
