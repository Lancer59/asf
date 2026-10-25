"""Local-first FastAPI app for Agent Skill Foundry."""
from __future__ import annotations

import json
import os
import re
import uuid
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

import agent
import skills

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
app = FastAPI(title="Agent Skill Foundry", version="0.1.0")
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
    base_url: str
    api_key: str = ""
    model: str


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"configured": bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY"))})


@app.get("/api/config")
def configuration():
    return {"llm_configured": bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY")), "search_configured": bool(os.getenv("TAVILY_API_KEY")),
            "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.openai.com/v1"),
            "model": os.getenv("LLM_MODEL", "gpt-4o-mini"), "docker_available": bool(__import__("shutil").which("docker"))}


@app.put("/api/config/provider")
def save_provider_config(payload: ProviderConfig):
    base_url = payload.base_url.strip().rstrip("/")
    model_name = payload.model.strip()
    if not base_url.startswith(("https://", "http://")) or not model_name:
        raise HTTPException(422, "Enter a valid http(s) endpoint URL and model name")
    api_key = payload.api_key.strip() or os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(422, "Enter an API key")
    env_path = ROOT / ".env"
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value
    values.update({"OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": api_key, "LLM_MODEL": model_name})
    env_path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    os.environ.update({"OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": api_key, "LLM_MODEL": model_name})
    return {"ok": True, "llm_configured": True, "base_url": base_url, "model": model_name}


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
    MESSAGES.clear()
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
    return draft


@app.post("/api/skills/drafts/{draft_id}/test")
def test_draft(draft_id: str, payload: DraftInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    draft["skill"].update(payload.skill)
    result = skills.test_code(draft["skill"].get("code", ""), payload.inputs, draft_id) if draft["skill"].get("skill_type") in {"python", "hybrid"} else {"success": True, "stdout": "Instructions reviewed; no Python was run.", "stderr": "", "error": None, "exit_code": 0, "duration_ms": 0, "source_hash": ""}
    draft["attempts"].append({"result": result, "skill": dict(draft["skill"])})
    return result


@app.post("/api/skills/drafts/{draft_id}/review")
def review_draft(draft_id: str, payload: ReviewInput):
    draft = skills.DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft expired")
    draft["skill"].update(payload.skill)
    if payload.feedback.strip():
        return {"status": "needs_revision", "message": "Feedback saved. Revise the fields, then run fresh tests before approval."}
    if payload.action == "reject":
        skills.audit(None, "rejected", {"draft_id": draft_id, "feedback": ""})
        skills.DRAFTS.pop(draft_id, None)
        return {"status": "rejected", "message": "Draft discarded. You can retry with the original request or a different query."}
    if payload.action != "accept":
        raise HTTPException(422, "action must be accept or reject")
    draft["accept_inputs"] = payload.inputs or {"user_input": draft["request"]}
    if draft["skill"].get("skill_type") in {"python", "hybrid"}:
        tested = [attempt for attempt in draft["attempts"] if attempt["skill"].get("code") == draft["skill"].get("code")]
        if not tested or not tested[-1]["result"]["success"]:
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
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY")):
        raise HTTPException(503, "Configure an OpenAI-compatible provider in Settings to request an agent revision")
    try:
        from langchain_openai import ChatOpenAI
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

        options = {"model": os.getenv("LLM_MODEL", "gpt-4o-mini"), "api_key": os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY"), "temperature": 0}
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_COMPAT_BASE_URL") or ("https://api.groq.com/openai/v1" if os.getenv("GROQ_API_KEY") and not os.getenv("OPENAI_API_KEY") else None)
        if base_url: options["base_url"] = base_url
        model = ChatOpenAI(**options).with_structured_output(SkillRevision)
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
        raise HTTPException(502, f"The model could not produce a structured revision: {type(exc).__name__}: {exc}") from exc
