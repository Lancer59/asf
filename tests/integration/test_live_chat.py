"""Opt-in live test of the configured model, agent tools, and HTTP SSE stream.

Run with ``ASF_LIVE_TESTS=1 python -m pytest -m live``. This makes a real
provider request and may incur usage charges.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.live


def _provider_ready() -> bool:
    load_dotenv(ROOT / ".env", override=False)
    return any(os.getenv(name) for name in (
        "OPENAI_API_KEY", "OPENAI_COMPAT_API_KEY", "GROQ_API_KEY"
    ))


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_live_model_calls_approved_skill_tool_and_finishes_sse(tmp_path):
    if os.getenv("ASF_LIVE_TESTS") != "1":
        pytest.skip("Set ASF_LIVE_TESTS=1 to enable provider-backed integration tests")
    if not _provider_ready():
        pytest.skip("Configure an OpenAI-compatible endpoint and API key in the environment or .env")

    port = _unused_local_port()
    url = f"http://127.0.0.1:{port}"
    child_env = os.environ.copy()
    child_env["PORT"] = str(port)
    child_env["LLM_MAX_TOKENS"] = "256"
    log_path = tmp_path / "uvicorn-live-test.log"
    server_code = (
        f"import os, sys; sys.path.insert(0, {str(ROOT)!r}); "
        "import app, uvicorn; "
        "uvicorn.run(app.app, host='127.0.0.1', port=int(os.environ['PORT']), log_level='warning')"
    )

    with log_path.open("w+", encoding="utf-8") as server_log:
        process = subprocess.Popen(
            [sys.executable, "-c", server_code],
            cwd=ROOT,
            env=child_env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            server_ready = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    health = httpx.get(url + "/api/config", timeout=1)
                    if health.status_code == 200:
                        server_ready = True
                        break
                except httpx.HTTPError:
                    time.sleep(0.2)
            if not server_ready:
                server_log.flush()
                server_log.seek(0)
                log = server_log.read()
                pytest.fail(f"Local app did not start for the live test. Server log:\n{log[-4000:]}")

            response_events = []
            with httpx.stream(
                "POST",
                url + "/api/chat/stream",
                json={"message": "Call the list_approved_skills tool before answering. Then say exactly: live tool call verified."},
                timeout=httpx.Timeout(connect=5, read=120, write=10, pool=5),
            ) as response:
                assert response.status_code == 200, response.read().decode("utf-8", errors="replace")
                assert response.headers.get("content-type", "").startswith("text/event-stream")
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        response_events.append(event)
                        if event.get("type") == "done":
                            break

            tool_starts = [event for event in response_events if event.get("type") == "tool_start"]
            tool_ends = [event for event in response_events if event.get("type") == "tool_end"]
            assert any(event.get("name") == "list_approved_skills" for event in tool_starts), response_events
            assert any(event.get("name") == "list_approved_skills" for event in tool_ends), response_events
            done = next((event for event in reversed(response_events) if event.get("type") == "done"), None)
            assert done is not None, response_events
            assert not done.get("error"), done
            assert done.get("reply", "").strip(), done
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
