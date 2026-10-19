#!/usr/bin/env sh
# one-command start for mac / linux: makes a venv, installs the light requirements, serves the dashboard
set -e
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install -q -r requirements-app.txt
echo "PyroScan on http://localhost:8000"
python -m uvicorn api.main:app --port 8000
