"""Run the local Agent Skill Foundry UI with the active Python environment."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

missing = [name for name in ("fastapi", "uvicorn", "jinja2", "dotenv", "yaml") if importlib.util.find_spec(name) is None]
if missing:
    print("Missing app dependencies: " + ", ".join(missing))
    print("From this folder run: python -m pip install -r requirements.txt")
    raise SystemExit(1)

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import uvicorn

if not (ROOT / ".env").exists():
    print("Setup: copy .env.example to .env and add GROQ_API_KEY, TAVILY_API_KEY, and LLM_MODEL.")
print("Agent Skill Foundry: http://127.0.0.1:8000")
print("Press Ctrl+C to stop.")
uvicorn.run("app:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
