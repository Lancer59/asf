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
FOUNDRY_MESSAGES: list[dict] = []
RUNNER_MESSAGES: list[dict] = []
# A Foundry transcript owns at most one active, unapproved draft. Clearing the
# transcript resets this session state; approved skills remain in the library.
FOUNDRY_ACTIVE_DRAFT_ID: str | None = None
FOUNDRY_SESSION_SKILL_NAME: str | None = None
FOUNDRY_PENDING_CLARIFICATION = False
# Backward-compatible alias for the old generic chat endpoint, now Skill Runner.
MESSAGES = RUNNER_MESSAGES


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


def _active_skill_session_reply(message: str) -> str | None:
    if (FOUNDRY_ACTIVE_DRAFT_ID is None or FOUNDRY_PENDING_CLARIFICATION
            or not agent._explicit_skill_request(message)):
        return None
    return f"This conversation is focused on {FOUNDRY_SESSION_SKILL_NAME or 'the current skill'}. Clear conversation to start another skill."


def _record_foundry_clarification_result(result: dict) -> None:
    global FOUNDRY_PENDING_CLARIFICATION
    FOUNDRY_PENDING_CLARIFICATION = bool(result.get("needs_clarification"))


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
    inputs: dict = Field(default_factory=dict)


class AgentRevisionInput(BaseModel):
    feedback: str
    current_skill: dict | None = None


class ProviderConfig(BaseModel):
    provider_type: str = llm_provider.OPENAI_COMPATIBLE
    base_url: str
    api_key: str = ""
    model: str
    api_version: str = ""


class ModelSelection(BaseModel):
    model_id: str


class ModelProfileConfig(BaseModel):
    model_id: str
    create: bool = False
    label: str
    provider_type: str
    base_url: str
    model: str
    api_key_env: str
    api_key: str = ""
    api_version: str = ""


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"configured": llm_provider.provider_configured()})


@app.get("/foundry")
def foundry_home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"configured": llm_provider.provider_configured()})


@app.get("/runner")
def runner_home(request: Request):
    return templates.TemplateResponse(request, "runner.html", {"configured": llm_provider.provider_configured()})




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
        "active_model_id": selected["model_id"],
        "base_url": selected["base_url"],
        "model": selected["model"],
        "api_version": selected["api_version"],
        "azure_openai": azure,
        "openai_compatible": compatible,
        "models": llm_provider.model_catalog(),
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
        active_id = llm_provider.DEFAULT_MODEL_ID
    if provider_type == llm_provider.AZURE_OPENAI:
        active_id = llm_provider.AZURE_MODEL_ID
    values["ASF_ACTIVE_MODEL"] = active_id
    os.environ["ASF_ACTIVE_MODEL"] = active_id
    env_path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    endpoint_host = urlsplit(base_url).hostname or "configured"
    logger.info("provider_configuration_saved provider=%s model=%s endpoint_host=%s", provider_type, model_name, endpoint_host)
    return {"ok": True, "llm_configured": True, "provider_type": provider_type, "base_url": base_url, "model": model_name, "api_version": api_version}


@app.put("/api/config/model")
def select_model(payload: ModelSelection):
    profile = next((item for item in llm_provider.model_profiles() if item["id"] == payload.model_id), None)
    if profile is None:
        raise HTTPException(404, "Model option not found")
    if not llm_provider.profile_configured(profile):
        raise HTTPException(422, "Configure this model's API key and endpoint before selecting it")
    env_path = ROOT / ".env"
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value
    values["ASF_ACTIVE_MODEL"] = profile["id"]
    values["ASF_LLM_PROVIDER"] = profile["provider_type"]
    env_path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    os.environ["ASF_ACTIVE_MODEL"] = profile["id"]
    os.environ["ASF_LLM_PROVIDER"] = profile["provider_type"]
    logger.info("model_selected model_id=%s provider=%s model=%s", profile["id"], profile["provider_type"], profile["model"])
    return {
        "ok": True,
        "model_id": profile["id"],
        "label": profile["label"],
        "provider_type": profile["provider_type"],
        "model": profile["model"],
    }


@app.put("/api/config/model-profile")
def save_model_profile(payload: ModelProfileConfig):
    """Create or edit an ASF_MODELS profile without exposing its secret."""
    profile = next((item for item in llm_provider.model_profiles() if item["id"] == payload.model_id), None)
    creating = payload.create
    if not creating and (profile is None or profile.get("legacy")):
        raise HTTPException(404, "Custom model profile not found")
    provider_type = payload.provider_type.strip().lower()
    if provider_type not in {llm_provider.OPENAI_COMPATIBLE, llm_provider.AZURE_OPENAI}:
        raise HTTPException(422, "Choose OpenAI-compatible or Azure OpenAI")
    base_url = payload.base_url.strip().rstrip("/")
    model_name = payload.model.strip()
    key_env = payload.api_key_env.strip()
    if not base_url.startswith(("https://", "http://")) or not model_name:
        raise HTTPException(422, "Enter a valid http(s) endpoint and model name")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise HTTPException(422, "API key environment variable must be a valid variable name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", payload.model_id):
        raise HTTPException(422, "Profile ID must start with a letter or number and use only letters, numbers, dots, underscores, or hyphens")
    label = payload.label.strip() or model_name

    env_path = ROOT / ".env"
    raw_catalog = os.getenv("ASF_MODELS", "").strip()
    try:
        catalog = json.loads(raw_catalog) if raw_catalog else []
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "ASF_MODELS is not valid JSON") from exc
    if not isinstance(catalog, list):
        raise HTTPException(422, "ASF_MODELS must be a JSON array")
    raw_profile = next((item for item in catalog if isinstance(item, dict) and item.get("id") == payload.model_id), None)
    if profile and raw_profile is None:
        raise HTTPException(409, "Restart ASF to load the latest ASF_MODELS catalog before editing it")
    if creating and any(item["id"] == payload.model_id for item in llm_provider.model_profiles()):
        raise HTTPException(409, "That profile ID is already in use")
    if not creating and not profile:
        raise HTTPException(404, "Custom model profile not found")
    if not profile and raw_profile:
        raise HTTPException(409, "That profile ID is already in use")
    updated = dict(raw_profile or {})
    updated.update({"id": payload.model_id, "label": label, "provider_type": provider_type,
                    "model": model_name, "api_key_env": key_env})
    if provider_type == llm_provider.AZURE_OPENAI:
        updated.pop("base_url", None)
        updated["endpoint"] = base_url + "/"
        updated["api_version"] = payload.api_version.strip() or "2024-08-01-preview"
    else:
        updated.pop("endpoint", None)
        updated["base_url"] = base_url
        updated.pop("api_version", None)
    if raw_profile:
        catalog = [updated if isinstance(item, dict) and item.get("id") == payload.model_id else item for item in catalog]
    else:
        catalog.append(updated)
    if len(catalog) > 100:
        raise HTTPException(422, "ASF_MODELS supports at most 100 custom profiles")
    catalog_json = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    if len(catalog_json) > 32768:
        raise HTTPException(422, "ASF_MODELS must be 32 KiB or smaller")
    raw_env_lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
    writes = {"ASF_MODELS": json.dumps(catalog_json), "ASF_ACTIVE_MODEL": payload.model_id,
              "ASF_LLM_PROVIDER": provider_type}
    if payload.api_key.strip():
        writes[key_env] = payload.api_key.strip()
    for key, value in writes.items():
        os.environ[key] = catalog_json if key == "ASF_MODELS" else value
    seen = set()
    output_lines = []
    for line in raw_env_lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in writes:
                if key not in seen:
                    output_lines.append(f"{key}={writes[key]}")
                    seen.add(key)
                continue
        output_lines.append(line)
    for key, value in writes.items():
        if key not in seen:
            output_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    logger.info("model_profile_saved model_id=%s provider=%s model=%s", payload.model_id, provider_type, model_name)
    return {"ok": True, "model_id": payload.model_id, "label": label, "provider_type": provider_type,
            "model": model_name, "configured": llm_provider.profile_configured(next(
                item for item in llm_provider.model_profiles() if item["id"] == payload.model_id))}


@app.post("/api/config/model-profile")
def create_model_profile(payload: ModelProfileConfig):
    if any(item["id"] == payload.model_id for item in llm_provider.model_profiles()):
        raise HTTPException(409, "That profile ID is already in use")
    payload.create = True
    return save_model_profile(payload)


@app.get("/api/skills")
def list_skills(q: str = "", status: str = ""):
    return skills.list_skills(q, status)


@app.get("/api/skills/{slug}")
def get_skill(slug: str):
    result = skills.get_skill(slug)
    if not result:
        raise HTTPException(404, "Skill not found")
    return result


def _chat_stream(payload: ChatInput, workspace: str):
    global FOUNDRY_ACTIVE_DRAFT_ID, FOUNDRY_SESSION_SKILL_NAME, FOUNDRY_PENDING_CLARIFICATION
    text = payload.message.strip()
    if not text:
        raise HTTPException(422, "Message cannot be empty")
    messages = FOUNDRY_MESSAGES if workspace == "foundry" else RUNNER_MESSAGES
    if workspace == "foundry":
        blocked_reply = _active_skill_session_reply(text)
        if blocked_reply:
            messages.append({"role": "user", "content": text})
            reply = blocked_reply
            messages.append({"role": "assistant", "content": reply})
            async def blocked_stream():
                yield "data: " + json.dumps({"type": "done", "reply": reply, "session_blocked": True}, ensure_ascii=False) + "\n\n"
            return StreamingResponse(blocked_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    logger.info("chat_request_accepted workspace=%s message_chars=%s history_messages=%s", workspace, len(text), len(messages))
    messages.append({"role": "user", "content": text})
    del messages[:-20]
    async def stream():
        global FOUNDRY_ACTIVE_DRAFT_ID, FOUNDRY_SESSION_SKILL_NAME, FOUNDRY_PENDING_CLARIFICATION
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event):
            await queue.put(event)

        continuation = workspace == "foundry" and FOUNDRY_PENDING_CLARIFICATION
        task = asyncio.create_task(agent.stream_agent(
            text, messages[:-1], emit, workspace=workspace,
            clarification_already_used=continuation,
        ))
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.15)
                yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
        result = await task
        if workspace == "foundry":
            _record_foundry_clarification_result(result)
        reply = result.get("reply", "")
        created_draft = None
        draft_id = result.get("created_draft_id")
        if draft_id:
            created_draft = skills.DRAFTS.get(draft_id)
            logger.info(
                "chat_draft_lookup draft_id=%s status=%s draft_count=%s",
                draft_id, "found" if created_draft else "missing", len(skills.DRAFTS),
            )
        elif any(step.get("name") == "propose_reusable_skill" for step in result.get("steps", [])):
            logger.warning("chat_draft_lookup status=no_created_draft draft_count=%s", len(skills.DRAFTS))
        if created_draft:
            if workspace == "foundry":
                FOUNDRY_ACTIVE_DRAFT_ID = created_draft["id"]
                FOUNDRY_SESSION_SKILL_NAME = created_draft.get("skill", {}).get("name")
            logger.info("chat_draft_sse_delivery_started draft_id=%s skill_type=%s", draft_id, created_draft.get("skill", {}).get("skill_type", "unknown"))
            logger.info("chat_draft_sse_payload draft_id=%s payload=%s", draft_id, json.dumps(created_draft, ensure_ascii=False, default=str))
            yield "data: " + json.dumps({"type": "draft", "draft": created_draft}, ensure_ascii=False, default=str) + "\n\n"
            yield "data: " + json.dumps({"type": "critique_start", "draft_id": created_draft["id"]}, ensure_ascii=False) + "\n\n"
            critique = await asyncio.to_thread(agent.critique_skill_draft_once, created_draft)
            yield "data: " + json.dumps({"type": "critique", "draft_id": created_draft["id"], **critique}, ensure_ascii=False, default=str) + "\n\n"
            logger.info("chat_draft_sse_delivery_finished draft_id=%s status=sent", draft_id)
        elif workspace == "foundry" and result.get("suggest_skill") and not _has_matching_approved_skill(" ".join(m.get("content", "") for m in messages[-6:])):
            yield "data: " + json.dumps({"type": "skill_offer", "request": text}, ensure_ascii=False) + "\n\n"
        messages.append({"role": "assistant", "content": reply})
        yield "data: " + json.dumps({"type": "done", "reply": reply, "error": result.get("error")}, ensure_ascii=False) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/foundry/chat/stream")
def foundry_chat_stream(payload: ChatInput):
    return _chat_stream(payload, "foundry")


@app.post("/api/runner/chat/stream")
def runner_chat_stream(payload: ChatInput):
    return _chat_stream(payload, "runner")


@app.post("/api/chat/stream")
def chat_stream(payload: ChatInput):
    """Compatibility alias for the former generic chat route (Skill Runner)."""
    return _chat_stream(payload, "runner")


def _clear_chat(workspace: str):
    global FOUNDRY_ACTIVE_DRAFT_ID, FOUNDRY_SESSION_SKILL_NAME, FOUNDRY_PENDING_CLARIFICATION
    messages = FOUNDRY_MESSAGES if workspace == "foundry" else RUNNER_MESSAGES
    previous_messages = len(messages)
    messages.clear()
    if workspace == "foundry":
        draft_id = FOUNDRY_ACTIVE_DRAFT_ID
        if draft_id:
            skills.DRAFTS.pop(draft_id, None)
        FOUNDRY_ACTIVE_DRAFT_ID = None
        FOUNDRY_SESSION_SKILL_NAME = None
        FOUNDRY_PENDING_CLARIFICATION = False
        logger.info("foundry_skill_session_cleared unapproved_draft_removed=%s", bool(draft_id))
    logger.info("chat_history_cleared workspace=%s messages_removed=%s", workspace, previous_messages)
    return {"ok": True}


@app.post("/api/foundry/chat/clear")
def clear_foundry_chat():
    return _clear_chat("foundry")


@app.post("/api/runner/chat/clear")
def clear_runner_chat():
    return _clear_chat("runner")


@app.post("/api/chat/clear")
def clear_chat():
    return _clear_chat("runner")


@app.post("/api/skills/drafts")
def create_draft(payload: ChatInput):
    global FOUNDRY_ACTIVE_DRAFT_ID, FOUNDRY_SESSION_SKILL_NAME
    if FOUNDRY_ACTIVE_DRAFT_ID is not None:
        raise HTTPException(409, "This conversation already has a skill. Clear conversation to start another skill.")
    draft = agent._skill_proposal(payload.message)
    FOUNDRY_ACTIVE_DRAFT_ID = draft["id"]
    FOUNDRY_SESSION_SKILL_NAME = draft.get("skill", {}).get("name")
    return draft


@app.put("/api/skills/drafts/{draft_id}")
def edit_draft(draft_id: str, payload: DraftInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    draft["skill"].update(payload.skill)
    draft.pop("automatic_critique", None)
    logger.info("skill_draft_edited draft_id=%s fields=%s", draft_id, len(payload.skill))
    return draft


@app.post("/api/skills/drafts/{draft_id}/test")
def test_draft(draft_id: str, payload: DraftInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    draft["skill"].update(payload.skill)
    draft.pop("automatic_critique", None)
    result = skills.test_code(draft["skill"].get("code", ""), payload.inputs, draft_id) if draft["skill"].get("skill_type") in {"python", "hybrid"} else {"success": True, "stdout": "Instructions reviewed; no Python was run.", "stderr": "", "error": None, "exit_code": 0, "duration_ms": 0, "source_hash": ""}
    draft["attempts"].append({"result": result, "skill": dict(draft["skill"])})
    logger.info(
        "skill_draft_test_finished draft_id=%s skill_type=%s success=%s status=%s",
        draft_id, draft["skill"].get("skill_type"), result.get("success"), result.get("status", "reviewed"),
    )
    return result


@app.post("/api/skills/drafts/{draft_id}/critique")
async def critique_draft(draft_id: str):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    return await asyncio.to_thread(agent.critique_skill_draft_once, draft)


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
    result = skills.update_skill(slug, payload.content, inputs=payload.inputs or None)
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
        from pydantic import BaseModel, ConfigDict

        class SkillRevision(BaseModel):
            model_config = ConfigDict(extra="forbid")

            name: str
            description: str
            skill_type: str
            instructions: str
            code: str
            # Strict OpenAI-compatible JSON schemas cannot contain arbitrary
            # object keys. Encode flexible skill metadata as JSON strings and
            # decode it after structured output validation.
            inputs_json: str
            expected_output: str
            dependencies: list[str]
            permissions: list[str]
            tags: list[str]
            category: str
            tests_json: str
            tools: list[str]

        model = llm_provider.create_chat_model(stream_usage=False).with_structured_output(SkillRevision)
        revised = model.invoke("Revise the reusable skill based on feedback. Keep request values in inputs. "
            "Return inputs_json as a JSON-encoded object string and tests_json as a JSON-encoded array of test objects. "
            "Use skill_type='python' when the user's feedback requests a code skill. Python/hybrid source must define synchronous run(inputs), call any helpers from it, and return a JSON-serializable value; never provide main()-only or print-only code. Keep external permissions disabled unless explicitly asked. "
            f"Original task: {draft['request']}\nCurrent draft: {json.dumps(draft['skill'])}\nFeedback: {payload.feedback}")
        revised_dict = revised.model_dump()
        try:
            revised_dict["inputs"] = json.loads(revised_dict.pop("inputs_json"))
            revised_dict["tests"] = json.loads(revised_dict.pop("tests_json"))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("The structured revision returned invalid JSON for inputs or tests.") from exc
        if not isinstance(revised_dict["inputs"], dict) or not isinstance(revised_dict["tests"], list) or any(not isinstance(test, dict) for test in revised_dict["tests"]):
            raise ValueError("The structured revision must contain an inputs object and a list of test objects.")
        if revised_dict["skill_type"] in {"python", "hybrid"}:
            contract_error = skills.python_entrypoint_error(revised_dict["code"])
            if contract_error:
                raise ValueError(contract_error)
        before = draft["skill"]
        draft["skill"] = revised_dict
        draft.pop("automatic_critique", None)
        draft["revision_diff"] = {key: {"before": before.get(key), "after": revised_dict.get(key)} for key in revised_dict if before.get(key) != revised_dict.get(key)}
        draft["attempts"].clear()
        return {"status": "revised", "draft": draft, "diff": draft["revision_diff"]}
    except Exception as exc:
        logger.error("skill_revision_failed error_type=%s", type(exc).__name__)
        raise HTTPException(502, f"The model could not produce a structured revision: {type(exc).__name__}: {exc}") from exc


@app.post("/api/skills/{slug}/agent-revision")
def revise_approved_skill(slug: str, payload: AgentRevisionInput):
    item = skills.get_skill(slug)
    if not item:
        raise HTTPException(404, "Skill not found")
    if not payload.feedback.strip():
        raise HTTPException(422, "Describe the change you want the agent to make")
    current = payload.current_skill or item["content"]
    draft_id = uuid.uuid4().hex
    temporary = {
        "id": draft_id,
        "request": f"Revise the approved skill {item['name']} while preserving its identity",
        "skill": current,
        "attempts": [],
    }
    skills.DRAFTS[draft_id] = temporary
    try:
        response = revise_draft(draft_id, ReviewInput(action="revise", skill={}, feedback=payload.feedback))
        revised = response["draft"]["skill"]
        # Skill identity is the library slug; content edits must not silently
        # create a second library entry under a changed name.
        revised["name"] = item["name"]
        before = current
        diff = {key: {"before": before.get(key), "after": revised.get(key)}
                for key in revised if before.get(key) != revised.get(key)}
        logger.info("approved_skill_agent_revision_proposed slug=%s changed_fields=%s", slug, ",".join(diff))
        return {"status": "revision_proposed", "skill": revised, "diff": diff}
    finally:
        skills.DRAFTS.pop(draft_id, None)
