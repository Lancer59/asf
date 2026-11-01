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
    monkeypatch.setenv("ASF_SANDBOX_BACKEND", "local")

    assert client.get("/").status_code == 200
    config = client.get("/api/config").json()
    assert config["llm_configured"] is False
    assert config["search_configured"] is False
    assert config["docker_available"] is False
    assert config["sandbox_backend"] == "local"
    assert "not isolated" in config["sandbox_warning"]


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
        "ASF_LLM_PROVIDER=openai_compatible\nOPENAI_BASE_URL=https://llm.example/v1\nOPENAI_API_KEY=secret\nLLM_MODEL=test-model\nASF_ACTIVE_MODEL=openai-compatible\n"
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
    assert "ASF_ACTIVE_MODEL=azure-openai" in saved
    assert app.configuration()["provider_type"] == "azure_openai"
    assert "AZURE_OPENAI_API_KEY" not in app.configuration()


def test_custom_model_catalog_can_be_selected_and_persists_without_exposing_key(client, isolated_project, monkeypatch):
    import json

    profiles = [{
        "id": "fast-local",
        "label": "Fast local model",
        "provider_type": "openai_compatible",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "api_key_env": "LOCAL_LLM_KEY",
    }]
    monkeypatch.setenv("ASF_MODELS", json.dumps(profiles))
    monkeypatch.setenv("LOCAL_LLM_KEY", "local-secret")
    (isolated_project / ".env").write_text("ASF_MODELS=" + json.dumps(profiles) + "\n", encoding="utf-8")

    catalog = client.get("/api/config").json()
    assert any(option["id"] == "fast-local" and option["configured"] for option in catalog["models"])
    assert any(option.get("api_key_env") == "LOCAL_LLM_KEY" for option in catalog["models"] if option["id"] == "fast-local")
    assert "local-secret" not in json.dumps(catalog)
    response = client.put("/api/config/model", json={"model_id": "fast-local"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "model_id": "fast-local", "label": "Fast local model",
        "provider_type": "openai_compatible", "model": "qwen3:8b",
    }
    assert app.configuration()["active_model_id"] == "fast-local"
    assert "ASF_ACTIVE_MODEL=fast-local" in (isolated_project / ".env").read_text(encoding="utf-8")
    assert "local-secret" not in json.dumps(response.json())


def test_custom_model_profile_can_be_corrected_to_azure_and_saved(client, isolated_project, monkeypatch):
    import json

    profiles = [{
        "id": "mid-level", "label": "GPT-5.3", "provider_type": "openai_compatible",
        "base_url": "https://servicedeskgpt.openai.azure.com/", "model": "gpt-5.3-chat",
        "api_key_env": "AZURE_OPENAI_API_KEY",
    }]
    monkeypatch.setenv("ASF_MODELS", json.dumps(profiles))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-secret")
    (isolated_project / ".env").write_text("ASF_MODELS=" + json.dumps(profiles) + "\nAZURE_OPENAI_API_KEY=azure-secret\n", encoding="utf-8")

    response = client.put("/api/config/model-profile", json={
        "model_id": "mid-level", "label": "GPT-5.3", "provider_type": "azure_openai",
        "base_url": "https://servicedeskgpt.openai.azure.com/", "model": "gpt-5.3-chat",
        "api_key_env": "AZURE_OPENAI_API_KEY", "api_version": "v1",
    })

    assert response.status_code == 200
    assert response.json()["provider_type"] == "azure_openai"
    assert response.json()["configured"] is True
    from dotenv import dotenv_values
    saved = dotenv_values(isolated_project / ".env")
    saved_profile = json.loads(saved["ASF_MODELS"])[0]
    assert saved_profile["provider_type"] == "azure_openai"
    assert saved_profile["endpoint"] == "https://servicedeskgpt.openai.azure.com/"
    assert saved_profile["api_version"] == "v1"
    assert saved["ASF_ACTIVE_MODEL"] == "mid-level"
    assert "azure-secret" not in json.dumps(response.json())


def test_can_add_multiple_openai_compatible_and_azure_profiles(client, isolated_project, monkeypatch):
    import json
    from dotenv import dotenv_values

    monkeypatch.setenv("ASF_MODELS", "[]")
    monkeypatch.setenv("LOCAL_MODEL_KEY", "local-secret")
    monkeypatch.setenv("AZURE_DEMO_KEY", "azure-secret")
    (isolated_project / ".env").write_text("ASF_MODELS=[]\n", encoding="utf-8")
    profiles = [
        {
            "model_id": "local-chat", "create": True, "label": "Local chat",
            "provider_type": "openai_compatible", "base_url": "http://localhost:11434/v1",
            "model": "qwen3:8b", "api_key_env": "LOCAL_MODEL_KEY", "api_key": "local-secret",
        },
        {
            "model_id": "azure-demo-2", "create": True, "label": "Azure demo 2",
            "provider_type": "azure_openai", "base_url": "https://another.openai.azure.com/",
            "model": "gpt-demo", "api_key_env": "AZURE_DEMO_KEY", "api_key": "azure-secret", "api_version": "v1",
        },
    ]

    responses = [client.post("/api/config/model-profile", json=profile) for profile in profiles]

    assert [response.status_code for response in responses] == [200, 200]
    saved = dotenv_values(isolated_project / ".env")
    persisted = json.loads(saved["ASF_MODELS"])
    assert [(profile["id"], profile["provider_type"]) for profile in persisted] == [
        ("local-chat", "openai_compatible"), ("azure-demo-2", "azure_openai")
    ]
    assert persisted[1]["endpoint"] == "https://another.openai.azure.com/"
    assert persisted[1]["api_version"] == "v1"
    assert saved["ASF_ACTIVE_MODEL"] == "azure-demo-2"
    assert "local-secret" not in json.dumps([response.json() for response in responses])
    assert "azure-secret" not in json.dumps([response.json() for response in responses])


def test_custom_model_without_its_key_cannot_be_selected(client, monkeypatch):
    import json

    monkeypatch.setenv("ASF_MODELS", json.dumps([{
        "id": "missing-key", "provider_type": "openai_compatible", "base_url": "https://model.example/v1",
        "model": "private-model", "api_key_env": "MISSING_MODEL_KEY",
    }]))
    response = client.put("/api/config/model", json={"model_id": "missing-key"})

    assert response.status_code == 422


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


def test_foundry_and_runner_have_separate_pages_histories_and_clear_routes(client):
    foundry = client.get("/foundry")
    runner = client.get("/runner")
    assert foundry.status_code == 200 and "Skill Foundry" in foundry.text
    assert runner.status_code == 200 and "Skill Runner" in runner.text
    assert "draft-host" in foundry.text
    assert 'class="foundry-layout"' in foundry.text
    assert "Clear conversation to start another skill" in foundry.text
    assert "draft-host" not in runner.text

    assert client.post("/api/foundry/chat/stream", json={"message": "Create a skill"}).status_code == 200
    assert client.post("/api/runner/chat/stream", json={"message": "Calculate 2 + 2"}).status_code == 200
    assert [item["content"] for item in app.FOUNDRY_MESSAGES] == ["Create a skill"]
    assert [item["content"] for item in app.RUNNER_MESSAGES] == ["Calculate 2 + 2"]

    assert client.post("/api/foundry/chat/clear").json() == {"ok": True}
    assert app.FOUNDRY_MESSAGES == []
    assert [item["content"] for item in app.RUNNER_MESSAGES] == ["Calculate 2 + 2"]
    assert client.post("/api/runner/chat/clear").json() == {"ok": True}
    assert app.RUNNER_MESSAGES == []


def test_clearing_foundry_resets_active_skill_draft_and_clarification(client):
    draft = skills.propose_skill("Create a test skill")
    app.FOUNDRY_ACTIVE_DRAFT_ID = draft["id"]
    app.FOUNDRY_PENDING_CLARIFICATION = True
    app.FOUNDRY_MESSAGES.extend([{"role": "user", "content": "Create it"}])

    assert client.post("/api/foundry/chat/clear").json() == {"ok": True}
    assert app.FOUNDRY_MESSAGES == []
    assert app.FOUNDRY_ACTIVE_DRAFT_ID is None
    assert app.FOUNDRY_PENDING_CLARIFICATION is False
    assert draft["id"] not in skills.DRAFTS


def test_foundry_clarification_is_allowed_once_then_active_skill_requires_clear(client):
    assert app._active_skill_session_reply("Create a date parsing skill") is None
    app._record_foundry_clarification_result({"needs_clarification": True})
    assert app.FOUNDRY_PENDING_CLARIFICATION is True

    # A user reply during the clarification round is not blocked as a second skill.
    assert app._active_skill_session_reply("Use Basic Auth") is None
    app._record_foundry_clarification_result({"needs_clarification": False})
    assert app.FOUNDRY_PENDING_CLARIFICATION is False

    app.FOUNDRY_ACTIVE_DRAFT_ID = "already-reviewed"
    app.FOUNDRY_SESSION_SKILL_NAME = "Date parser"
    assert app._active_skill_session_reply("Create another skill") == (
        "This conversation is focused on Date parser. Clear conversation to start another skill."
    )


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


def test_revision_schema_is_strict_compatible_and_restores_dynamic_inputs_and_tests(client, monkeypatch):
    draft = skills.propose_skill("Create a reusable skill")
    monkeypatch.setattr(app.llm_provider, "provider_configured", lambda: True)
    revised_payload = {
        "name": "code-skill", "description": "A code skill", "skill_type": "python",
        "instructions": "Run code", "code": "def run(inputs): return inputs['value']",
        "inputs_json": '{"value":{"type":"integer"}}', "expected_output": "The value",
        "dependencies": [], "permissions": [], "tags": ["code"], "category": "utility",
        "tests_json": '[{"name":"identity","inputs":{"value":3},"expected":3}]', "tools": [],
    }
    captured = {}

    class FakeModel:
        def with_structured_output(self, schema):
            captured["schema"] = schema.model_json_schema()
            captured["model"] = schema(**revised_payload)
            return self

        def invoke(self, _prompt):
            return captured["model"]

    monkeypatch.setattr(app.llm_provider, "create_chat_model", lambda **_kwargs: FakeModel())
    response = client.post(f"/api/skills/drafts/{draft['id']}/revise", json={"action": "revise", "skill": {}, "feedback": "it should be a code skill"})

    assert response.status_code == 200, response.text
    schema = captured["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["inputs_json"]["type"] == "string"
    assert schema["properties"]["tests_json"]["type"] == "string"
    revised = response.json()["draft"]["skill"]
    assert revised["skill_type"] == "python"
    assert revised["inputs"] == {"value": {"type": "integer"}}
    assert revised["tests"] == [{"name": "identity", "inputs": {"value": 3}, "expected": 3}]


def test_agent_revision_of_approved_skill_returns_proposal_without_saving(client, monkeypatch):
    original = skills.propose_skill("Create an HTTP helper")
    original["skill"].update({"name": "HTTP Helper", "description": "Original description"})
    approved = skills.promote(original)
    slug = approved["skill"]["slug"]
    monkeypatch.setattr(app.llm_provider, "provider_configured", lambda: True)
    revised_payload = {
        "name": "Renamed by model", "description": "Updated description", "skill_type": "instruction",
        "instructions": "Send requests safely", "code": "", "inputs_json": '{"url":{"type":"string"}}',
        "expected_output": "A response", "dependencies": [], "permissions": [], "tags": ["http"],
        "category": "api", "tests_json": '[{"name":"status","inputs":{"url":"https://example.test"},"expected":"A response"}]',
        "tools": [],
    }

    class FakeModel:
        def with_structured_output(self, schema):
            self.revision = schema(**revised_payload)
            return self

        def invoke(self, _prompt):
            return self.revision

    monkeypatch.setattr(app.llm_provider, "create_chat_model", lambda **_kwargs: FakeModel())
    draft_ids_before = set(skills.DRAFTS)
    response = client.post(f"/api/skills/{slug}/agent-revision", json={"feedback": "Update the response behavior"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "revision_proposed"
    assert response.json()["skill"]["description"] == "Updated description"
    assert response.json()["skill"]["name"] == "HTTP Helper"  # Preserve library identity.
    unchanged = skills.get_skill(slug)
    assert unchanged["version"] == 1
    assert unchanged["content"]["description"] == "Original description"
    assert set(skills.DRAFTS) == draft_ids_before


def test_draft_python_test_requires_successful_current_source_before_accept(client, monkeypatch):
    draft = skills.propose_skill("Create a python skill")
    draft["skill"].update({"name": "python-check", "skill_type": "python", "code": "def run(inputs): return 1"})
    monkeypatch.setattr(skills, "test_code", lambda *args: {"success": False, "status": "failed", "error": "test failed"})
    tested = client.post(f"/api/skills/drafts/{draft['id']}/test", json={"skill": {}, "inputs": {"x": 1}})
    assert tested.json()["success"] is False
    result = client.post(f"/api/skills/drafts/{draft['id']}/review", json={"action": "accept", "skill": {}, "inputs": {}})
    assert result.json()["status"] == "test_required"
    assert client.post(f"/api/skills/drafts/expired/test", json={"skill": {}, "inputs": {}}).status_code == 404
