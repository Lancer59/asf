from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import app


def _write_log(path, lines, modified):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    timestamp = modified.timestamp()
    import os
    os.utime(path, (timestamp, timestamp))


def test_log_file_list_is_newest_first_and_selects_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setenv("ASF_LOG_FILE", "logs/asf.log")
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 1, 2, tzinfo=timezone.utc)
    _write_log(tmp_path / "logs/asf.log.1", ["older"], older)
    _write_log(tmp_path / "logs/asf.log", ["newer"], newer)

    result = app.list_log_files()

    assert [item["name"] for item in result["files"]] == ["asf.log", "asf.log.1"]
    assert result["selected"] == "asf.log"


def test_log_dashboard_displays_generated_from_logs_warning_and_controls():
    from starlette.requests import Request
    request = Request({
        "type": "http", "method": "GET", "path": "/logs", "headers": [],
        "query_string": b"", "server": ("test", 80), "scheme": "http",
        "client": ("test", 1), "root_path": "",
    })

    response = app.log_dashboard(request)
    html = response.body.decode("utf-8")

    assert "generated from local ASF log files" in html
    assert 'id="file-select"' in html
    assert 'id="download"' in html
    assert "Download HTML snapshot" in html
    assert 'id="count-prompt-tokens"' in html
    assert 'id="count-tool-runs"' in html
    assert "Token totals are provider-reported" in html


def test_selected_log_returns_newest_entries_and_rejects_unlisted_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setenv("ASF_LOG_FILE", "logs/asf.log")
    _write_log(tmp_path / "logs/asf.log", [
        "2026-01-01T00:00:00Z INFO asf.agent request_id=abc first_event",
        "2026-01-01T00:00:01Z ERROR asf.http request_id=def second_event",
    ], datetime(2026, 1, 1, tzinfo=timezone.utc))

    result = app.read_log_file("asf.log", limit=1)

    assert result["total_entries"] == 2
    assert result["truncated"] is True
    assert result["entries"] == [{
        "timestamp": "2026-01-01T00:00:01Z", "level": "ERROR", "logger": "asf.http",
        "request_id": "def", "message": "second_event", "fields": {},
    }]
    with pytest.raises(HTTPException) as exc:
        app.read_log_file("..", limit=1)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        app.read_log_file("secrets.env", limit=1)
    assert exc.value.status_code == 404


def test_log_file_api_returns_newest_lines_first(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setenv("ASF_LOG_FILE", "asf.log")
    _write_log(tmp_path / "asf.log", [f"line {number}" for number in range(3)], datetime.now(timezone.utc))

    result = app.read_log_file("asf.log", limit=3)

    assert [entry["message"] for entry in result["entries"]] == ["line 2", "line 1", "line 0"]


def test_ai_metrics_cover_full_log_even_when_visible_rows_are_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setenv("ASF_LOG_FILE", "asf.log")
    _write_log(tmp_path / "asf.log", [
        "2026-01-01T00:00:00Z INFO asf.agent request_id=stream-1 agent_stream_started message_chars=5 history_messages=0",
        "2026-01-01T00:00:01Z INFO asf.agent request_id=stream-1 agent_model_call_finished model=test status=success duration_ms=10 prompt_tokens=10 completion_tokens=5 total_tokens=15 token_usage_reported=true",
        "2026-01-01T00:00:02Z INFO asf.agent request_id=stream-1 agent_tool_started tool=lookup",
        "2026-01-01T00:00:03Z INFO asf.agent request_id=stream-1 agent_tool_finished tool=lookup status=success duration_ms=2",
        "2026-01-01T00:00:04Z INFO asf.agent request_id=stream-1 agent_stream_finished status=success duration_ms=20 model_calls=1 prompt_tokens=10 completion_tokens=5 token_reports=1 tool_runs=1 tool_failures=0 skill_suggested=false",
        "2026-01-01T00:00:05Z INFO asf.agent request_id=invoke-2 agent_invocation_started message_chars=5 history_messages=0",
        "2026-01-01T00:00:06Z INFO asf.agent request_id=invoke-2 agent_invocation_finished status=success duration_ms=15 trace_messages=2 model_calls=2 prompt_tokens=4 completion_tokens=3 token_reports=1 tool_runs=1 tool_failures=unknown",
        "2026-01-01T00:00:07Z INFO asf.http request_id=http-1 http_request_finished method=POST path=/api/chat status=200 duration_ms=10",
        "2026-01-01T00:00:08Z ERROR asf.http request_id=http-2 http_request_finished method=POST path=/api/chat status=500 duration_ms=30",
    ], datetime(2026, 1, 1, tzinfo=timezone.utc))

    result = app.read_log_file("asf.log", limit=1)

    assert result["returned_entries"] == 1
    assert result["ai_metrics"] == {
        "http_requests": 2, "http_failures": 1, "http_avg_duration_ms": 20,
        "agent_runs": 2, "agent_completed": 2, "agent_failed": 0, "agent_incomplete": 0,
        "model_calls": 3, "model_failures": 0, "prompt_tokens": 14,
        "completion_tokens": 8, "total_tokens": 22, "usage_missing": 1,
        "tool_runs": 2, "tool_failures": 0,
        "tool_counts": {"lookup": {"runs": 1, "failures": 0}, "unknown": {"runs": 1, "failures": 0}},
    }
