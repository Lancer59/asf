import app
import skills


def test_home_and_config_report_unconfigured_provider(client, monkeypatch):
    for key in ("OPENAI_API_KEY", "OPENAI_COMPAT_API_KEY", "GROQ_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ASF_LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT_NAME", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)

    assert client.get("/").status_code == 200
    config = client.get("/api/config").json()
    assert config["llm_configured"] is False
    assert config["search_configured"] is False
    assert config["docker_available"] is False


def test_provider_config_validation_and_save(client, monkeypatch, isolated_project):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    bad_url = client.put("/api/config/provider", json={"base_url": "file:///tmp", "model": "m", "api_key": "k"})
    missing_key = client.put("/api/config/provider", json={"base_url": "https://llm.example/v1", "model": "m"})
    assert bad_url.status_code == 422
    assert missing_key.status_code == 422

    response = client.put("/api/config/provider", json={
        "base_url": " https://llm.example/v1/ ", "model": " test-model ", "api_key": " secret "
    })
    assert response.status_code == 200
    assert response.json() == {"ok": True, "llm_configured": True, "provider_type": "openai_compatible", "base_url": "https://llm.example/v1", "model": "test-model", "api_version": ""}
    assert (isolated_project / ".env").read_text(encoding="utf-8") == (
        "ASF_LLM_PROVIDER=openai_compatible\nOPENAI_BASE_URL=https://llm.example/v1\nOPENAI_API_KEY=secret\nLLM_MODEL=test-model\n"
    )
    assert app.configuration()["llm_configured"] is True


def test_provider_config_preserves_existing_env_entries(client, isolated_project, monkeypatch):
    (isolated_project / ".env").write_text("TAVILY_API_KEY=search-secret\n# comment\nOTHER=value\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    response = client.put("/api/config/provider", json={"base_url": "http://localhost:8000/v1", "model": "local", "api_key": "key"})
    assert response.status_code == 200
    saved = (isolated_project / ".env").read_text(encoding="utf-8")
    assert "TAVILY_API_KEY=search-secret" in saved
    assert "OTHER=value" in saved
    assert "OPENAI_BASE_URL=http://localhost:8000/v1" in saved


def test_azure_provider_saves_separate_profile_and_does_not_replace_openai_key(client, isolated_project, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-profile-secret")
    (isolated_project / ".env").write_text(
        "OPENAI_API_KEY=openai-profile-secret\nOPENAI_BASE_URL=https://openai.example/v1\n",
        encoding="utf-8",
    )
    response = client.put("/api/config/provider", json={
        "provider_type": "azure_openai",
        "base_url": "https://servicedeskgpt.openai.azure.com/",
        "api_key": "azure-profile-secret",
        "api_version": "2024-08-01-preview",
        "model": "gpt-5.3-chat",
    })

    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "llm_configured": True, "provider_type": "azure_openai",
        "base_url": "https://servicedeskgpt.openai.azure.com/",
        "model": "gpt-5.3-chat", "api_version": "2024-08-01-preview",
    }
    saved = (isolated_project / ".env").read_text(encoding="utf-8")
    assert "ASF_LLM_PROVIDER=azure_openai" in saved
    assert "AZURE_OPENAI_API_KEY=azure-profile-secret" in saved
    assert "AZURE_OPENAI_ENDPOINT=https://servicedeskgpt.openai.azure.com/" in saved
    assert "AZURE_OPENAI_API_VERSION=2024-08-01-preview" in saved
    assert "AZURE_OPENAI_DEPLOYMENT_NAME=gpt-5.3-chat" in saved
    assert "OPENAI_API_KEY=openai-profile-secret" in saved
    assert app.configuration()["provider_type"] == "azure_openai"
    assert "AZURE_OPENAI_API_KEY" not in app.configuration()


def test_chat_rejects_blank_message(client):
    response = client.post("/api/chat/stream", json={"message": "   "})
    assert response.status_code == 422
    assert app.MESSAGES == []


def test_chat_route_returns_sse_response_and_persists_user_message(client):
    response = client.post("/api/chat/stream", json={"message": "Search the web"})
    assert response.status_code == 200
    assert response.data.media_type == "text/event-stream"
    assert response.data.headers["Cache-Control"] == "no-cache"
    assert app.MESSAGES == [{"role": "user", "content": "Search the web"}]


def test_chat_history_is_bounded_and_clear_removes_it(client):
    for index in range(25):
        client.post("/api/chat/stream", json={"message": f"m{index}"})
    assert len(app.MESSAGES) == 20
    assert app.MESSAGES[0]["content"] == "m5"
    assert client.post("/api/chat/clear").json() == {"ok": True}
    assert app.MESSAGES == []


def test_skills_routes_filter_and_return_not_found(client, monkeypatch):
    monkeypatch.setattr(app.skills, "list_skills", lambda q="", status="": [{"slug": "json-validator", "status": "approved"}])
    monkeypatch.setattr(app.skills, "get_skill", lambda slug: None)
    assert client.get("/api/skills?status=approved").json() == [{"slug": "json-validator", "status": "approved"}]
    assert client.get("/api/skills/missing").status_code == 404
    assert client.patch("/api/skills/missing", json={"content": {}}).status_code == 404
    assert client.post("/api/skills/missing/test", json={"skill": {}, "inputs": {}}).status_code == 404
    assert client.post("/api/skills/missing/deprecate").status_code == 404


def test_draft_review_requires_fresh_passing_test_and_accepts_instruction(client, monkeypatch):
    draft = skills.propose_skill("A reusable instruction")
    draft["skill"].update({"name": "instruction-demo", "skill_type": "instruction", "instructions": "Do the task."})
    no_test = client.post(f"/api/skills/drafts/{draft['id']}/review", json={"action": "accept", "skill": {}, "inputs": {}})
    assert no_test.status_code == 200
    assert no_test.json()["status"] == "approved"
    approved = no_test.json()["skill"]
    assert approved["status"] == "approved"
    assert (skills.APPROVED / "instruction-demo" / "SKILL.md").exists()

    stale = skills.propose_skill("a python capability")
    stale["skill"].update({"name": "python-demo", "skill_type": "python", "code": "def run(inputs): return 1"})
    rejected = client.post(f"/api/skills/drafts/{stale['id']}/review", json={"action": "accept", "skill": {}, "inputs": {}})
    assert rejected.json()["status"] == "test_required"


def test_draft_endpoints_edit_test_revise_reject_and_expiry(client, monkeypatch):
    created = client.post("/api/skills/drafts", json={"message": "Create a skill for checking logs"})
    draft = created.json()
    assert created.status_code == 200 and draft["id"]
    edited = client.put(f"/api/skills/drafts/{draft['id']}", json={"skill": {"name": "Log Checker"}})
    assert edited.json()["skill"]["name"] == "Log Checker"
    tested = client.post(f"/api/skills/drafts/{draft['id']}/test", json={"skill": {"skill_type": "instruction"}, "inputs": {}})
    assert tested.json()["success"] is True
    needs_revision = client.post(f"/api/skills/drafts/{draft['id']}/review", json={"action": "accept", "skill": {}, "feedback": "make clearer"})
    assert needs_revision.json()["status"] == "needs_revision"
    missing_feedback = client.post(f"/api/skills/drafts/{draft['id']}/revise", json={"action": "revise", "skill": {}, "feedback": "  "})
    assert missing_feedback.status_code == 422
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    unavailable = client.post(f"/api/skills/drafts/{draft['id']}/revise", json={"action": "revise", "skill": {}, "feedback": "clarify"})
    assert unavailable.status_code == 503
    rejected = client.post(f"/api/skills/drafts/{draft['id']}/review", json={"action": "reject", "skill": {}, "feedback": ""})
    assert rejected.json()["status"] == "rejected"
    assert client.get(f"/api/skills/drafts/{draft['id']}").status_code == 404


def test_draft_python_test_requires_successful_current_source_before_accept(client, monkeypatch):
    draft = skills.propose_skill("Create a python skill")
    draft["skill"].update({"name": "python-check", "skill_type": "python", "code": "def run(inputs): return 1"})
    monkeypatch.setattr(skills, "test_code", lambda *args: {"success": False, "status": "failed", "error": "test failed"})
    tested = client.post(f"/api/skills/drafts/{draft['id']}/test", json={"skill": {}, "inputs": {"x": 1}})
    assert tested.json()["success"] is False
    result = client.post(f"/api/skills/drafts/{draft['id']}/review", json={"action": "accept", "skill": {}, "inputs": {}})
    assert result.json()["status"] == "test_required"
    assert client.post(f"/api/skills/drafts/expired/test", json={"skill": {}, "inputs": {}}).status_code == 404
