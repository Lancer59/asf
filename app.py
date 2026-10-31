"""Local-first FastAPI app for Agent Skill Foundry."""
from __future__ import annotations

import json
import os
import re
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from observability import RequestLoggingMiddleware, available_log_files, configure_logging
import llm_provider

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
logger = configure_logging(ROOT)
LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\S+)\s+(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<logger>\S+)\s+request_id=(?P<request_id>\S+)\s+(?P<message>.*)$"
)
LOG_FIELD_RE = re.compile(r"(?P<key>[a-z][a-z0-9_]*)=(?P<value>[^\s]+)")

import agent
import skills

app = FastAPI(title="Agent Skill Foundry", version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
MESSAGES: list[dict] = []


def _has_matching_approved_skill(text: str) -> bool:
    terms = set(re.findall(r"[a-z0-9]+", text.lower()))
    for item in skills.list_skills(status="approved"):
        metadata = item.get("metadata", {})
        skill_terms = set(re.findall(r"[a-z0-9]+", " ".join([
            item.get("name", ""), *metadata.get("tags", [])
        ]).lower()))
        # One distinctive domain tag (e.g. "json") is enough to avoid
        # repeatedly offering a skill the user has already approved.
        if terms & skill_terms:
            return True
    return False


class ChatInput(BaseModel):
    message: str


class DraftInput(BaseModel):
    skill: dict
    inputs: dict = Field(default_factory=dict)


class ReviewInput(BaseModel):
    action: str
    skill: dict
    feedback: str = ""
    inputs: dict = Field(default_factory=dict)


class EditInput(BaseModel):
    content: dict


class ProviderConfig(BaseModel):
    provider_type: str = llm_provider.OPENAI_COMPATIBLE
    base_url: str
    api_key: str = ""
    model: str
    api_version: str = ""


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"configured": llm_provider.provider_configured()})


@app.get("/logs")
def log_dashboard(request: Request):
    return templates.TemplateResponse(request, "logs.html", {})


@app.get("/api/logs")
def list_log_files():
    files = []
    for path in available_log_files(ROOT):
        stat = path.stat()
        files.append({
            "name": path.name,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return {"files": files, "selected": files[0]["name"] if files else None}


def _log_fields(message: str) -> dict[str, str]:
    return {field["key"]: field["value"] for field in LOG_FIELD_RE.finditer(message)}


def _int_field(fields: dict[str, str], name: str) -> int:
    try:
        return int(fields.get(name, ""))
    except (TypeError, ValueError):
        return 0


def summarize_ai_log(lines: list[str]) -> dict[str, object]:
    """Aggregate AI run and usage events over every line in one selected log file."""
    events = []
    for line in lines:
        match = LOG_LINE_RE.match(line)
        if not match:
            continue
        message = match.group("message")
        if message.startswith(("agent_stream_", "agent_invocation_", "agent_model_call_finished", "agent_tool_", "http_request_finished")):
            events.append({"message": message, "fields": _log_fields(message), "request_id": match.group("request_id")})

    def matching(prefix: str) -> list[dict]:
        return [event for event in events if event["message"].startswith(prefix)]

    starts = matching("agent_stream_started") + matching("agent_invocation_started")
    finishes = matching("agent_stream_finished") + matching("agent_invocation_finished")
    successful_runs = sum(event["fields"].get("status") == "success" for event in finishes)
    failed_runs = sum(event["fields"].get("status") == "error" for event in finishes)
    failed_runs += len(matching("agent_stream_unavailable")) + len(matching("agent_invocation_unavailable"))

    model_events = matching("agent_model_call_finished")
    invocation_summaries = matching("agent_invocation_finished")
    summary_only = [summary for summary in invocation_summaries if not any(call["request_id"] == summary["request_id"] for call in model_events)]
    prompt_tokens = sum(_int_field(event["fields"], "prompt_tokens") for event in model_events)
    completion_tokens = sum(_int_field(event["fields"], "completion_tokens") for event in model_events)
    total_tokens = sum(_int_field(event["fields"], "total_tokens") for event in model_events)
    prompt_tokens += sum(_int_field(event["fields"], "prompt_tokens") for event in summary_only)
    completion_tokens += sum(_int_field(event["fields"], "completion_tokens") for event in summary_only)
    total_tokens += sum(_int_field(event["fields"], "prompt_tokens") + _int_field(event["fields"], "completion_tokens") for event in summary_only)
    model_calls = len(model_events) + sum(_int_field(event["fields"], "model_calls") for event in summary_only)
    usage_missing = sum(event["fields"].get("token_usage_reported") == "false" for event in model_events)
    usage_missing += sum(max(0, _int_field(event["fields"], "model_calls") - _int_field(event["fields"], "token_reports")) for event in summary_only)

    tool_starts = matching("agent_tool_started")
    tool_finishes = matching("agent_tool_finished")
    explicit_tool_events = tool_starts + tool_finishes
    tools_summary_only = [summary for summary in invocation_summaries if not any(tool["request_id"] == summary["request_id"] for tool in explicit_tool_events)]
    tool_failures = sum(event["fields"].get("status") == "error" for event in tool_finishes)
    tool_counts: dict[str, dict[str, int]] = {}
    tool_names = {event["fields"].get("tool", "unknown") for event in explicit_tool_events}
    for name in tool_names:
        started_count = sum(event["fields"].get("tool", "unknown") == name for event in tool_starts)
        finish_events = [event for event in tool_finishes if event["fields"].get("tool", "unknown") == name]
        nonstream_completions = sum(event["fields"].get("status") == "completed" for event in finish_events)
        tool_counts[name] = {
            "runs": max(started_count, nonstream_completions),
            "failures": sum(event["fields"].get("status") == "error" for event in finish_events),
        }
    for summary in tools_summary_only:
        unlabelled_runs = _int_field(summary["fields"], "tool_runs")
        if unlabelled_runs:
            tool_counts.setdefault("unknown", {"runs": 0, "failures": 0})["runs"] += unlabelled_runs
    http_finishes = matching("http_request_finished")
    http_failures = sum((_int_field(event["fields"], "status") >= 400) for event in http_finishes)
    http_durations = [_int_field(event["fields"], "duration_ms") for event in http_finishes if "duration_ms" in event["fields"]]
    return {
        "http_requests": len(http_finishes),
        "http_failures": http_failures,
        "http_avg_duration_ms": round(sum(http_durations) / len(http_durations)) if http_durations else 0,
        "agent_runs": len(starts),
        "agent_completed": successful_runs,
        "agent_failed": failed_runs,
        "agent_incomplete": max(0, len(starts) - successful_runs - failed_runs),
        "model_calls": model_calls,
        "model_failures": sum(event["fields"].get("status") == "error" for event in model_events),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "usage_missing": usage_missing,
        "tool_runs": sum(item["runs"] for item in tool_counts.values()),
        "tool_failures": tool_failures,
        "tool_counts": tool_counts,
    }


@app.get("/api/logs/{filename}")
def read_log_file(filename: str, limit: int = Query(default=1000, ge=1, le=10000)):
    if Path(filename).name != filename:
        raise HTTPException(404, "Log file not found")
    path = next((candidate for candidate in available_log_files(ROOT) if candidate.name == filename), None)
    if path is None:
        raise HTTPException(404, "Log file not found")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected_lines = lines[-limit:][::-1]
    entries = []
    for line in selected_lines:
        match = LOG_LINE_RE.match(line)
        if match:
            entry = match.groupdict()
            entry["fields"] = _log_fields(entry["message"])
            entries.append(entry)
        else:
            entries.append({
                "timestamp": "", "level": "UNKNOWN", "logger": "-", "request_id": "-", "message": line, "fields": {},
            })
    stat = path.stat()
    return {
        "file": filename,
        "size_bytes": stat.st_size,
        "total_entries": len(lines),
        "returned_entries": len(entries),
        "truncated": len(lines) > len(entries),
        "ai_metrics": summarize_ai_log(lines),
        "entries": entries,
    }


@app.get("/api/config")
def configuration():
    selected = llm_provider.configured_display()
    azure = {
        "base_url": os.getenv("AZURE_OPENAI_ENDPOINT", "https://servicedeskgpt.openai.azure.com/"),
        "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.3-chat"),
        "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
    }
    compatible = {
        "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.openai.com/v1"),
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "api_version": "",
    }
    return {
        "llm_configured": llm_provider.provider_configured(),
        "provider_type": selected["provider_type"],
        "base_url": selected["base_url"],
        "model": selected["model"],
        "api_version": selected["api_version"],
        "azure_openai": azure,
        "openai_compatible": compatible,
        "search_configured": bool(os.getenv("TAVILY_API_KEY")),
        "docker_available": bool(__import__("shutil").which("docker")),
    }


@app.put("/api/config/provider")
def save_provider_config(payload: ProviderConfig):
    provider_type = payload.provider_type.strip().lower()
    if provider_type not in {llm_provider.OPENAI_COMPATIBLE, llm_provider.AZURE_OPENAI}:
        raise HTTPException(422, "Choose OpenAI-compatible or Azure OpenAI")
    base_url = payload.base_url.strip().rstrip("/")
    model_name = payload.model.strip()
    if not base_url.startswith(("https://", "http://")) or not model_name:
        raise HTTPException(422, "Enter a valid http(s) endpoint URL and model name")
    if provider_type == llm_provider.AZURE_OPENAI:
        api_key = payload.api_key.strip() or os.getenv("AZURE_OPENAI_API_KEY", "")
        api_version = payload.api_version.strip() or os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    else:
        api_key = payload.api_key.strip() or os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY")
        api_version = ""
    if not api_key:
        raise HTTPException(422, "Enter an API key")
    env_path = ROOT / ".env"
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value
    if provider_type == llm_provider.AZURE_OPENAI:
        azure_endpoint = base_url + "/"
        values.update({
            "ASF_LLM_PROVIDER": provider_type,
            "AZURE_OPENAI_ENDPOINT": azure_endpoint,
            "AZURE_OPENAI_API_KEY": api_key,
            "AZURE_OPENAI_API_VERSION": api_version,
            "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
        })
        os.environ.update({
            "ASF_LLM_PROVIDER": provider_type,
            "AZURE_OPENAI_ENDPOINT": azure_endpoint,
            "AZURE_OPENAI_API_KEY": api_key,
            "AZURE_OPENAI_API_VERSION": api_version,
            "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
        })
        base_url = azure_endpoint
    else:
        values.update({"ASF_LLM_PROVIDER": provider_type, "OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": api_key, "LLM_MODEL": model_name})
        os.environ.update({"ASF_LLM_PROVIDER": provider_type, "OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": api_key, "LLM_MODEL": model_name})
    env_path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    endpoint_host = urlsplit(base_url).hostname or "configured"
    logger.info("provider_configuration_saved provider=%s model=%s endpoint_host=%s", provider_type, model_name, endpoint_host)
    return {"ok": True, "llm_configured": True, "provider_type": provider_type, "base_url": base_url, "model": model_name, "api_version": api_version}


@app.get("/api/skills")
def list_skills(q: str = "", status: str = ""):
    return skills.list_skills(q, status)


@app.get("/api/skills/{slug}")
def get_skill(slug: str):
    result = skills.get_skill(slug)
    if not result:
        raise HTTPException(404, "Skill not found")
    return result


@app.post("/api/chat/stream")
def chat_stream(payload: ChatInput):
    text = payload.message.strip()
    if not text:
        raise HTTPException(422, "Message cannot be empty")
    logger.info("chat_request_accepted message_chars=%s history_messages=%s", len(text), len(MESSAGES))
    MESSAGES.append({"role": "user", "content": text})
    # Keep exactly one volatile, bounded process-local conversation.
    del MESSAGES[:-20]
    async def stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event):
            await queue.put(event)

        task = asyncio.create_task(agent.stream_agent(text, MESSAGES[:-1], emit))
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.15)
                yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
        result = await task
        reply = result.get("reply", "")
        created_draft = None
        for step in result.get("steps", []):
            if step.get("type") == "tool_start" and step.get("name") == "propose_reusable_skill":
                args = step.get("input", {})
                task_text = args.get("task", text) if isinstance(args, dict) else text
                draft = next((d for d in skills.DRAFTS.values() if d.get("request") == task_text), None)
                if draft:
                    created_draft = draft
        if created_draft:
            yield "data: " + json.dumps({"type": "draft", "draft": created_draft}, ensure_ascii=False, default=str) + "\n\n"
        elif result.get("suggest_skill") and not _has_matching_approved_skill(" ".join(m.get("content", "") for m in MESSAGES[-6:])):
            yield "data: " + json.dumps({"type": "skill_offer", "request": text}, ensure_ascii=False) + "\n\n"
        MESSAGES.append({"role": "assistant", "content": reply})
        yield "data: " + json.dumps({"type": "done", "reply": reply, "error": result.get("error")}, ensure_ascii=False) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/clear")
def clear_chat():
    previous_messages = len(MESSAGES)
    MESSAGES.clear()
    logger.info("chat_history_cleared messages_removed=%s", previous_messages)
    return {"ok": True}


@app.post("/api/skills/drafts")
def create_draft(payload: ChatInput):
    return agent._skill_proposal(payload.message)


@app.put("/api/skills/drafts/{draft_id}")
def edit_draft(draft_id: str, payload: DraftInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    draft["skill"].update(payload.skill)
    logger.info("skill_draft_edited draft_id=%s fields=%s", draft_id, len(payload.skill))
    return draft


@app.post("/api/skills/drafts/{draft_id}/test")
def test_draft(draft_id: str, payload: DraftInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    draft["skill"].update(payload.skill)
    result = skills.test_code(draft["skill"].get("code", ""), payload.inputs, draft_id) if draft["skill"].get("skill_type") in {"python", "hybrid"} else {"success": True, "stdout": "Instructions reviewed; no Python was run.", "stderr": "", "error": None, "exit_code": 0, "duration_ms": 0, "source_hash": ""}
    draft["attempts"].append({"result": result, "skill": dict(draft["skill"])})
    logger.info(
        "skill_draft_test_finished draft_id=%s skill_type=%s success=%s status=%s",
        draft_id, draft["skill"].get("skill_type"), result.get("success"), result.get("status", "reviewed"),
    )
    return result


@app.post("/api/skills/drafts/{draft_id}/review")
def review_draft(draft_id: str, payload: ReviewInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    draft["skill"].update(payload.skill)
    if payload.feedback.strip():
        logger.info("skill_draft_feedback_received draft_id=%s feedback_chars=%s", draft_id, len(payload.feedback.strip()))
        return {"status": "needs_revision", "message": "Feedback saved. Revise the fields, then run fresh tests before approval."}
    if payload.action == "reject":
        skills.audit(None, "rejected", {"draft_id": draft_id, "feedback": ""})
        skills.DRAFTS.pop(draft_id, None)
        logger.info("skill_draft_rejected draft_id=%s", draft_id)
        return {"status": "rejected", "message": "Draft discarded. You can retry with the original request or a different query."}
    if payload.action != "accept":
        logger.warning("skill_draft_review_invalid_action draft_id=%s", draft_id)
        raise HTTPException(422, "action must be accept or reject")
    draft["accept_inputs"] = payload.inputs or {"user_input": draft["request"]}
    if draft["skill"].get("skill_type") in {"python", "hybrid"}:
        tested = [attempt for attempt in draft["attempts"] if attempt["skill"].get("code") == draft["skill"].get("code")]
        if not tested or not tested[-1]["result"]["success"]:
            logger.warning("skill_draft_acceptance_blocked draft_id=%s reason=fresh_passing_test_required", draft_id)
            return {"status": "test_required", "message": "Run and pass a fresh test for this exact source before approval."}
    result = skills.promote(draft)
    if result["status"] == "approved":
        skills.DRAFTS.pop(draft_id, None)
    return result


@app.patch("/api/skills/{slug}")
def patch_skill(slug: str, payload: EditInput):
    result = skills.update_skill(slug, payload.content)
    if result is None:
        raise HTTPException(404, "Skill not found")
    return result


@app.post("/api/skills/{slug}/test")
def test_skill(slug: str, payload: DraftInput):
    item = skills.get_skill(slug)
    if not item:
        raise HTTPException(404, "Skill not found")
    content = {**item["content"], **payload.skill}
    if content.get("skill_type") not in {"python", "hybrid"}:
        logger.info("skill_test_finished slug=%s skill_type=%s status=instruction_ready", slug, content.get("skill_type"))
        return {"success": True, "stdout": "Instruction skill ready for agent use.", "stderr": "", "duration_ms": 0}
    return skills.test_code(content.get("code", ""), payload.inputs, slug)


@app.post("/api/skills/{slug}/deprecate")
def deprecate_skill(slug: str):
    result = skills.deprecate(slug)
    if not result:
        raise HTTPException(404, "Skill not found")
    return result


@app.get("/api/skills/drafts/{draft_id}")
def get_draft(draft_id: str):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    return draft


@app.post("/api/skills/drafts/{draft_id}/revise")
def revise_draft(draft_id: str, payload: ReviewInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    if not payload.feedback.strip():
        raise HTTPException(422, "Feedback is required")
    if not llm_provider.provider_configured():
        raise HTTPException(503, "Configure an OpenAI-compatible provider in Settings to request an agent revision")
    try:
        from pydantic import BaseModel, Field

        class SkillRevision(BaseModel):
            name: str
            description: str
            skill_type: str
            instructions: str
            code: str
            inputs: dict
            expected_output: str
            dependencies: list[str]
            permissions: list[str]
            tags: list[str]
            category: str
            tests: list[dict]
            tools: list[str]

        model = llm_provider.create_chat_model(stream_usage=False).with_structured_output(SkillRevision)
        revised = model.invoke("Revise the reusable skill based on feedback. Keep request values in inputs. "
            "Provide complete Python source only for python/hybrid types; keep all external permissions disabled unless explicitly asked. "
            f"Original task: {draft['request']}\nCurrent draft: {json.dumps(draft['skill'])}\nFeedback: {payload.feedback}")
        revised_dict = revised.model_dump()
        before = draft["skill"]
        draft["skill"] = revised_dict
        draft["revision_diff"] = {key: {"before": before.get(key), "after": revised_dict.get(key)} for key in revised_dict if before.get(key) != revised_dict.get(key)}
        draft["attempts"].clear()
        return {"status": "revised", "draft": draft, "diff": draft["revision_diff"]}
    except Exception as exc:
        logger.error("skill_revision_failed error_type=%s", type(exc).__name__)
        raise HTTPException(502, f"The model could not produce a structured revision: {type(exc).__name__}: {exc}") from exc
