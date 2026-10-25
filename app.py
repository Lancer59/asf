"""Local-first FastAPI app for Agent Skill Foundry."""
from __future__ import annotations

import json
import os
import uuid
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


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"configured": bool(os.getenv("GROQ_API_KEY") and os.getenv("TAVILY_API_KEY"))})


@app.get("/api/config")
def configuration():
    return {"llm_configured": bool(os.getenv("GROQ_API_KEY")), "search_configured": bool(os.getenv("TAVILY_API_KEY")),
            "model": os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"), "docker_available": bool(__import__("shutil").which("docker"))}


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
    result = agent.invoke_agent(text, MESSAGES[:-1])
    if result.get("error"):
        reply = result["error"]
    else:
        reply = result.get("reply", "")
    if not result.get("error"):
        for step in result.get("steps", []):
            calls = step.get("tool_calls", []) if isinstance(step, dict) else []
            for call in calls:
                if call.get("name") == "propose_reusable_skill":
                    args = call.get("args", {})
                    task = args.get("task", text)
                    draft = next((d for d in skills.DRAFTS.values() if d.get("request") == task), None)
                    if draft:
                        result["draft"] = draft
    MESSAGES.append({"role": "assistant", "content": reply})
    event = {"type": "done", "reply": reply, "steps": result.get("steps", []), "error": result.get("error"), "draft": result.get("draft")}
    def stream():
        yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/chat/clear")
def clear_chat():
    MESSAGES.clear()
    return {"ok": True}


@app.post("/api/skills/drafts")
def create_draft(payload: ChatInput):
    return skills.propose_skill(payload.message)


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
    if not os.getenv("GROQ_API_KEY"):
        raise HTTPException(503, "Configure GROQ_API_KEY to request an agent revision")
    try:
        from langchain_groq import ChatGroq
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

        model = ChatGroq(model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"), temperature=0).with_structured_output(SkillRevision)
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
