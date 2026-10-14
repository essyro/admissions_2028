#!/bin/zsh
set -e
cd "${0:A:h}"
exec "$PWD/.venv/bin/python" -m uvicorn app:app --app-dir "$PWD" --host 127.0.0.1 --port "${PORT:-8768}"