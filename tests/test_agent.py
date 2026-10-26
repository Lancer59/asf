import ast

import agent
import app
import skills


def test_compact_history_keeps_bounded_recent_turns():
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "x" * 2000},
        {"role": "user", "content": "recent 1"},
        {"role": "assistant", "content": "recent 2"},
        {"role": "user", "content": "recent 3"},
    ]

    compact = agent._compact_history(messages, limit=4)

    assert [item["content"] for item in compact[-2:]] == ["recent 2", "recent 3"]
    assert len(compact[0]["content"]) < 1700
    assert "trimmed" in compact[0]["content"]


def test_skill_offer_does_not_trigger_for_one_off_json_question():
    assert not agent._should_suggest_skill("Is this JSON valid?")
    assert agent._should_suggest_skill("Validate JSON documents and report errors")
    assert agent._should_suggest_skill("Create a skill for JSON validation")


def test_explicit_skill_request_is_detected_for_compact_chat_reply():
    assert agent._explicit_skill_request("I want a json validation skill please")
    assert not agent._explicit_skill_request("Is this JSON valid?")


def test_existing_approved_skill_suppresses_duplicate_offer(monkeypatch):
    monkeypatch.setattr(app.skills, "list_skills", lambda status="": [{
        "name": "JSON Validator", "description": "Validate JSON documents", "metadata": {"tags": ["json", "validation", "schema"]}
    }] if status == "approved" else [])

    assert app._has_matching_approved_skill("Check whether this JSON schema is valid")
    assert not app._has_matching_approved_skill("Summarize this meeting transcript")


def test_json_skill_proposal_has_runnable_code_and_examples():
    draft = agent._skill_proposal("Create a skill for JSON validation")
    try:
        skill = draft["skill"]
        ast.parse(skill["code"])
        assert skill["name"] == "JSON Validator"
        assert skill["inputs"]["json_text"]["type"] == "string"
        assert len(skill["tests"]) >= 2
    finally:
        skills.DRAFTS.pop(draft["id"], None)


def test_approved_skill_execution_uses_sandbox_and_returns_code_result(monkeypatch):
    monkeypatch.setattr(agent.skills, "get_skill", lambda slug: {
        "status": "approved", "content": {"skill_type": "python", "code": "def run(inputs): return {'valid': True}"}
    })
    captured = {}

    def run_in_sandbox(code, inputs, slug):
        captured.update(code=code, inputs=inputs, slug=slug)
        return {"success": True, "status": "passed", "return_value": {"valid": True}, "stdout": "", "user_stderr": ""}

    monkeypatch.setattr(agent.skills, "test_code", run_in_sandbox)
    inputs = {"json_text": '{"ok": true}'}

    result = agent.execute_approved_skill("json-validator", inputs)

    assert result["success"] and result["result"] == {"valid": True}
    assert captured == {"code": "def run(inputs): return {'valid': True}", "inputs": inputs, "slug": "json-validator"}


def test_tool_progress_labels_distinguish_skill_lookup_execution_and_drafts(monkeypatch):
    monkeypatch.setattr(agent.skills, "get_skill", lambda slug: {"status": "approved", "name": "JSON Validator"})
    assert agent.tool_event_label("list_approved_skills", "end") == "Approved skills checked"
    assert agent.tool_event_label("propose_reusable_skill", "end") == "Skill draft prepared"
    assert agent.tool_event_label("run_approved_skill", "start", {"slug": "json-validator"}) == "Running JSON Validator in the sandbox"
    assert agent.tool_event_label("run_approved_skill", "end", '{"skill_name":"JSON Validator","success":true,"status":"passed"}') == "JSON Validator executed successfully · sandbox passed"


def test_tool_progress_labels_cover_search_failure_and_sandbox_unavailable(monkeypatch):
    monkeypatch.setattr(agent.skills, "get_skill", lambda slug: None)
    assert agent.tool_event_label("web_search", "start") == "Searching the web"
    assert agent.tool_event_label("web_search", "end") == "Search results received"
    assert agent.tool_event_label("other", "start") == "Running a tool"
    assert agent.tool_event_label("other", "end") == "Tool finished"
    assert agent.tool_event_label("run_approved_skill", "end", '{"status":"sandbox_unavailable"}') == "Approved Skill was not run · sandbox unavailable"
    assert agent.tool_event_label("run_approved_skill", "end", '{"success":false,"status":"failed"}') == "Approved Skill execution failed"


