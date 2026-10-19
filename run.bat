@echo off
rem one-click start for windows: makes a venv, installs the light requirements, opens the dashboard
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate
python -m pip install -q -r requirements-app.txt
start "" http://localhost:8000
python -m uvicorn api.main:app --port 8000
