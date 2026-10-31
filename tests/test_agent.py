import ast
import json
import sys
from types import ModuleType, SimpleNamespace

import agent
import llm_provider
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


def test_partial_proposal_stream_yields_only_complete_fields():
    partial = '{"task":"Build a skill","proposal":{"name":"API Poster","description":"Send requests",'
    assert agent._completed_proposal_fields(partial) == {"name": "API Poster", "description": "Send requests"}

    nested = partial + '"inputs":{"url":{"type":"string"}},"tests":[{"name":"ok"}]'
    assert agent._completed_proposal_fields(nested) == {
        "name": "API Poster", "description": "Send requests",
        "inputs": {"url": {"type": "string"}},
    }
    with_array = nested + ',"category":"api"'
    assert agent._completed_proposal_fields(with_array)["tests"] == [{"name": "ok"}]


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
        assert "Input:" in skill["instructions"] and "Output:" in skill["instructions"]
        assert "def run(inputs)" in skill["code"]
    finally:
        skills.DRAFTS.pop(draft["id"], None)


def test_python_proposal_tests_match_run_inputs_and_return_shape():
    proposal = agent.SkillProposal(
        name="Post API", description="Send a post request", skill_type="python",
        instructions="Accept URL and return a result object.",
        code="def run(inputs):\n    return {'success': True, 'status_code': 200}",
        inputs={
            "url": {"type": "string", "required": True},
            "timeout": {"type": "integer", "default": 30},
        },
        dependencies=[], permissions=[], tags=[], category="api",
        tests=[{
            "name": "successful request",
            "inputs": {"url": "https://example.test"},
            "expected": {"success": True, "status_code": 200},
        }],
    )
    assert agent._proposal_issues(proposal) == []

    proposal.tests = [{
        "name": "bad input shape",
        "inputs": {"url": {"type": "string"}, "undeclared": 1},
        "expected": "success is true",
    }]
    issues = agent._proposal_issues(proposal)
    assert any("input 'url' must be a JSON string" in issue for issue in issues)
    assert any("undeclared names: undeclared" in issue for issue in issues)


def test_reusable_skill_proposal_uses_request_specific_name_and_model_candidate():
    draft = agent._skill_proposal("Create a skill for checking logs")
    assert draft["skill"]["name"] == "Log Checker"
    skills.DRAFTS.pop(draft["id"], None)

    candidate = {
        "name": "New reusable capability", "description": "Check logs", "skill_type": "python",
        "instructions": "Input: log_text. Output: findings object.",
        "code": "def normalize(text): return text.strip()\ndef run(inputs): return {'normalized': normalize(inputs['log_text'])}",
        "inputs": {"log_text": {"type": "string"}}, "expected_output": "Return normalized log text.",
        "dependencies": [], "permissions": [], "tests": [], "tags": ["logs"], "category": "logs", "tools": [],
    }
    draft = agent._skill_proposal("Create a reusable skill to check logs", json.dumps(candidate))
    try:
        assert draft["skill"]["name"] == "Log Checker"
        assert "def run(inputs)" in draft["skill"]["code"]
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


def test_create_agent_requires_provider_key(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_COMPAT_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    instance, error = agent.create_agent()

    assert instance is None
    assert "add an API key" in error


def test_create_agent_configures_compatible_model_and_registers_tools(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_MAX_TOKENS", "321")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **options):
            captured["model_options"] = options

    def create_deep_agent(**options):
        captured["agent_options"] = options
        return "fake-agent"

    deepagents = ModuleType("deepagents")
    deepagents.create_deep_agent = create_deep_agent
    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "deepagents", deepagents)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)
    monkeypatch.setattr(agent, "approved_skill_directories", lambda: ["/skills/approved/"])
    monkeypatch.setattr(agent, "approved_skill_files", lambda: {})
    monkeypatch.setattr(agent.skills, "list_skills", lambda **kwargs: [])

    instance, error = agent.create_agent()

    assert error is None
    assert instance == "fake-agent"
    assert captured["model_options"] == {
        "model": "test-model", "api_key": "test-key", "temperature": 0,
        "stream_usage": True, "max_tokens": 321, "base_url": "https://llm.example/v1",
    }
    options = captured["agent_options"]
    assert options["skills"] == ["/skills/approved/"]
    assert {tool.__name__ for tool in options["tools"]} == {
        "list_approved_skills", "run_approved_skill"
    }
    assert "do not list skills for every request" in options["system_prompt"]
    assert "Never claim code ran unless that tool returned success" in options["system_prompt"]


def test_create_agent_uses_azure_deployment_without_touching_openai_profile(monkeypatch):
    monkeypatch.setenv("ASF_LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://servicedeskgpt.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.3-chat")
    monkeypatch.setenv("OPENAI_API_KEY", "separate-openai-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    class FakeAzureChatOpenAI:
        def __init__(self, **options): captured["model_options"] = options

    def create_deep_agent(**options):
        captured["agent_options"] = options
        return "azure-agent"

    deepagents = ModuleType("deepagents")
    deepagents.create_deep_agent = create_deep_agent
    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.AzureChatOpenAI = FakeAzureChatOpenAI
    monkeypatch.setitem(sys.modules, "deepagents", deepagents)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)
    monkeypatch.setattr(agent, "approved_skill_directories", lambda: [])
    monkeypatch.setattr(agent, "approved_skill_files", lambda: {})
    monkeypatch.setattr(agent.skills, "list_skills", lambda **kwargs: [])

    instance, error = agent.create_agent()

    assert error is None
    assert instance == "azure-agent"
    assert captured["model_options"] == {
        "azure_endpoint": "https://servicedeskgpt.openai.azure.com",
        "azure_deployment": "gpt-5.3-chat",
        "api_version": "2024-08-01-preview",
        "api_key": "azure-key",
        "max_completion_tokens": 1200,
        "stream_usage": True,
    }
    assert "temperature" not in captured["model_options"]
    assert captured["agent_options"]["tools"]


def test_azure_v1_profile_uses_openai_v1_endpoint(monkeypatch):
    monkeypatch.setenv("ASF_LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://service.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "v1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "demo-deployment")
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **options): captured.update(options)

    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)

    llm_provider.create_chat_model()

    assert captured["model"] == "demo-deployment"
    assert captured["base_url"] == "https://service.openai.azure.com/openai/v1"
    assert captured["api_key"] == "azure-key"
    assert captured["max_completion_tokens"] == 1200
    assert "temperature" not in captured


def test_custom_compatible_profile_builds_client_using_its_key_env(monkeypatch):
    profile = [{
        "id": "fast-local", "label": "Fast local", "provider_type": "openai_compatible",
        "base_url": "http://localhost:11434/v1", "model": "qwen3:8b", "api_key_env": "LOCAL_LLM_KEY",
    }]
    monkeypatch.setenv("ASF_MODELS", json.dumps(profile))
    monkeypatch.setenv("ASF_ACTIVE_MODEL", "fast-local")
    monkeypatch.setenv("LOCAL_LLM_KEY", "local-secret")
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **options): captured.update(options)

    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)

    llm_provider.create_chat_model()

    assert captured == {
        "model": "qwen3:8b", "api_key": "local-secret", "base_url": "http://localhost:11434/v1",
        "stream_usage": True, "temperature": 0, "max_tokens": 1200,
    }


def test_model_catalog_ignores_invalid_and_duplicate_profiles(monkeypatch):
    monkeypatch.setenv("ASF_MODELS", json.dumps([
        {"id": "bad id", "provider_type": "openai_compatible", "base_url": "https://other.example/v1", "model": "other"},
        {"id": "valid-profile", "provider_type": "openai_compatible", "base_url": "https://other.example/v1", "model": "other"},
        {"id": "valid-profile", "provider_type": "openai_compatible", "base_url": "https://other.example/v1", "model": "other", "api_key_env": "OTHER_KEY"},
    ]))

    ids = [profile["id"] for profile in llm_provider.model_profiles()]

    assert "bad id" not in ids
    assert ids == ["valid-profile"]


def test_explicit_model_catalog_is_the_complete_picker_catalog(monkeypatch):
    monkeypatch.setenv("ASF_LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("ASF_MODELS", json.dumps([
        {"id": "primary", "label": "Primary", "provider_type": "openai_compatible", "base_url": "https://groq.example/v1", "model": "oss-model"},
        {"id": "Mid-Level", "label": "GPT-5.3", "provider_type": "azure_openai", "endpoint": "https://resource.openai.azure.com/", "model": "deployment", "api_version": "v1"},
    ]))

    assert [profile["id"] for profile in llm_provider.model_profiles()] == ["primary", "Mid-Level"]
    assert llm_provider.active_model_id() == "primary"


def test_create_agent_optional_search_tool_is_only_registered_when_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "search-key")
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **options): pass

    class FakeTavilySearch:
        def __init__(self, **options): captured["search_options"] = options
        def invoke(self, inputs): return {"answer": "summary", "results": [{"title": "A", "url": "https://example.com", "content": "snippet"}]}

    deepagents = ModuleType("deepagents")
    deepagents.create_deep_agent = lambda **options: captured.update(agent_options=options) or "fake-agent"
    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    langchain_tavily = ModuleType("langchain_tavily")
    langchain_tavily.TavilySearch = FakeTavilySearch
    monkeypatch.setitem(sys.modules, "deepagents", deepagents)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)
    monkeypatch.setitem(sys.modules, "langchain_tavily", langchain_tavily)
    monkeypatch.setattr(agent, "approved_skill_directories", lambda: [])

    instance, error = agent.create_agent()

    assert instance == "fake-agent" and error is None
    assert captured["search_options"] == {"max_results": 5}
    tools = captured["agent_options"]["tools"]
    assert tools[0].__name__ == "web_search"
    assert json.loads(tools[0]("test query")) == {
        "answer": "summary", "results": [{"title": "A", "url": "https://example.com", "snippet": "snippet"}]
    }


def test_agent_tool_closures_list_skills_propose_once_and_reject_unapproved_execution(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **options): pass

    deepagents = ModuleType("deepagents")
    deepagents.create_deep_agent = lambda **options: captured.update(options=options) or "fake-agent"
    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "deepagents", deepagents)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)
    monkeypatch.setattr(agent.skills, "list_skills", lambda **kwargs: [{
        "slug": "demo", "name": "Demo", "description": "A demo", "content": {"skill_type": "python", "inputs": {"x": {"type": "integer"}}}, "metadata": {"tags": ["sample"]}
    }])
    monkeypatch.setattr(agent, "approved_skill_directories", lambda: [])
    agent.DRAFTS.clear()
    _, error = agent.create_agent("foundry")
    assert error is None
    tools = {getattr(tool, "name", getattr(tool, "__name__", "")): tool for tool in captured["options"]["tools"]}
    assert json.loads(tools["list_approved_skills"]()) == [{
        "slug": "demo", "name": "Demo", "description": "A demo", "skill_type": "python", "inputs": {"x": {"type": "integer"}}, "expected_output": "", "instructions": "", "tags": ["sample"]
    }]
    proposed_code = "def run(inputs):\n    return {'answer': inputs['value']}"
    proposal = {
        "name": "Demo Skill", "description": "Return a value", "skill_type": "python",
        "instructions": "Input: value. Output: answer object.", "code": proposed_code,
        "inputs": {"value": {"type": "integer"}}, "expected_output": "An answer object.",
        "dependencies": [], "permissions": [], "tests": [{"name": "basic", "inputs": {"value": 3}, "expected": {"answer": 3}}],
        "tags": ["demo"], "category": "testing", "tools": [], "scope_note": "",
    }
    malformed = tools["propose_reusable_skill"].invoke({"task": "Create one demo skill", "proposal": {"name": "Incomplete"}})
    assert "No draft was created" in malformed
    assert "description" in malformed
    assert not agent.DRAFTS
    first = tools["propose_reusable_skill"].invoke({"task": "Create one demo skill", "proposal": proposal})
    second = tools["propose_reusable_skill"].invoke({"task": "This should not create another", "proposal": proposal})
    assert "Draft created" in first
    assert "already created" in second
    assert len(agent.DRAFTS) == 1
    draft = next(iter(agent.DRAFTS.values()))
    assert draft["skill"]["code"] == proposed_code
    assert draft["skill"]["inputs"] == {"value": {"type": "integer"}}
    # Optional descriptive fields and free-form tool metadata must not block draft creation.
    minimal_optional = dict(proposal)
    minimal_optional.pop("expected_output")
    minimal_optional["tools"] = ["HTTP POST"]
    minimal_optional["inputs"] = [
        {"name": key, **value} for key, value in proposal["inputs"].items()
    ]
    agent.DRAFTS.clear()
    _, error = agent.create_agent("foundry")
    assert error is None
    fresh_tools = {getattr(tool, "name", getattr(tool, "__name__", "")): tool for tool in captured["options"]["tools"]}
    optional_result = fresh_tools["propose_reusable_skill"].invoke({"task": "Create a POST capability", "proposal": minimal_optional})
    assert "Draft created" in optional_result
    optional_draft = next(iter(agent.DRAFTS.values()))
    assert optional_draft["skill"]["expected_output"]
    assert optional_draft["skill"]["tools"] == ["HTTP POST"]
    assert optional_draft["skill"]["inputs"] == proposal["inputs"]
    assert "run(inputs)" in captured["options"]["system_prompt"]
    assert "run_approved_skill" not in tools
    assert "Do not act as a general task assistant" in captured["options"]["system_prompt"]
    agent.DRAFTS.clear()


def test_foundry_and_runner_toolsets_are_separate(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **options): pass

    deepagents = ModuleType("deepagents")
    deepagents.create_deep_agent = lambda **options: captured.update(options=options) or "agent"
    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "deepagents", deepagents)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)
    monkeypatch.setattr(agent, "approved_skill_directories", lambda: ["/approved"])
    monkeypatch.setattr(agent, "approved_skill_files", lambda: {})

    _, error = agent.create_agent("foundry")
    assert error is None
    foundry = captured["options"]
    foundry_tools = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in foundry["tools"]}
    assert foundry_tools == {"list_approved_skills", "propose_reusable_skill"}
    assert foundry["skills"] == []

    _, error = agent.create_agent("runner")
    assert error is None
    runner = captured["options"]
    runner_tools = {tool.__name__ for tool in runner["tools"]}
    assert runner_tools == {"list_approved_skills", "run_approved_skill"}
    assert "propose_reusable_skill" not in runner_tools
    assert runner["skills"] == ["/approved"]


def test_invoke_agent_compacts_history_and_returns_model_tool_trace(monkeypatch):
    import logging

    recorded = {}
    records = []
    class Capture(logging.Handler):
        def emit(self, record): records.append(record.getMessage())

    handler = Capture()
    agent.logger.addHandler(handler)
    final_message = SimpleNamespace(type="ai", content="Answer from the tool", tool_calls=[])
    tool_message = SimpleNamespace(type="tool", content="[]", tool_calls=[], name="list_approved_skills")

    class FakeAgent:
        def invoke(self, payload):
            recorded["payload"] = payload
            return {"messages": [tool_message, final_message]}

    monkeypatch.setattr(agent, "create_agent", lambda workspace="runner": (FakeAgent(), None))
    monkeypatch.setattr(agent, "approved_skill_files", lambda: {"/skills/approved/demo/SKILL.md": "demo"})
    history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "x" * 1700}]

    try:
        result = agent.invoke_agent("current task", history)
    finally:
        agent.logger.removeHandler(handler)

    assert result["reply"] == "Answer from the tool"
    assert result["steps"][-1]["content"] == "Answer from the tool"
    payload = recorded["payload"]
    assert payload["messages"][-1] == {"role": "user", "content": "current task"}
    assert payload["messages"][1]["content"].endswith("[earlier content trimmed]")
    assert payload["files"] == {"/skills/approved/demo/SKILL.md": "demo"}
    assert any("agent_tool_finished tool=list_approved_skills status=completed" in line for line in records)


def test_token_usage_extractor_supports_langchain_and_openai_metadata():
    assert agent._extract_token_usage({"usage_metadata": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}}) == (12, 7, 19)
    assert agent._extract_token_usage({"response_metadata": {"token_usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}}) == (3, 4, 7)
    assert agent._extract_token_usage({}) == (None, None, None)


def test_agent_stream_logs_model_usage_and_tool_durations(monkeypatch):
    import logging
    from types import SimpleNamespace

    records = []
    class Capture(logging.Handler):
        def emit(self, record): records.append(record.getMessage())

    def run_immediate(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        raise AssertionError("Fake stream unexpectedly suspended")

    async def emit(_event):
        return None

    handler = Capture()
    agent.logger.addHandler(handler)

    class FakeAgent:
        async def astream_events(self, *_args, **_kwargs):
            events = [
                {"event": "on_chat_model_start", "run_id": "m1", "name": "ChatOpenAI", "data": {}},
                {"event": "on_chat_model_end", "run_id": "m1", "name": "ChatOpenAI", "data": {"output": SimpleNamespace(usage_metadata={"input_tokens": 11, "output_tokens": 5, "total_tokens": 16})}},
                {"event": "on_tool_start", "run_id": "t1", "name": "test_tool", "data": {"input": {}}},
                {"event": "on_tool_end", "run_id": "t1", "name": "test_tool", "data": {"output": "ok"}},
                {"event": "on_tool_start", "run_id": "t2", "name": "broken_tool", "data": {"input": {}}},
                {"event": "on_tool_error", "run_id": "t2", "name": "broken_tool", "data": {"error": ValueError("private detail")}},
            ]
            for event in events:
                yield event

    monkeypatch.setattr(agent, "create_agent", lambda workspace="runner": (FakeAgent(), None))
    try:
        run_immediate(agent.stream_agent("hello there", [], emit, workspace="runner"))
    finally:
        agent.logger.removeHandler(handler)

    assert any("agent_model_call_finished" in line and "prompt_tokens=11" in line and "completion_tokens=5" in line for line in records)
    assert any("agent_tool_finished tool=test_tool status=success" in line and "duration_ms=" in line for line in records)
    assert any("agent_tool_finished tool=broken_tool status=error" in line and "error_type=ValueError" in line for line in records)
    assert any('error="private detail"' in line for line in records)
    assert any("agent_stream_finished workspace=runner status=success" in line and "model_calls=1" in line and "tool_runs=2" in line and "tool_failures=1" in line for line in records)


def test_foundry_proposal_tool_errors_are_visible_and_bounded(monkeypatch):
    emitted = []

    async def emit(event):
        emitted.append(event)

    class FakeAgent:
        async def astream_events(self, *_args, **_kwargs):
            for run_id in ("t1", "t2", "t3"):
                yield {"event": "on_tool_start", "run_id": run_id, "name": "propose_reusable_skill", "data": {"input": {"task": "make a skill"}}}
                yield {"event": "on_tool_error", "run_id": run_id, "name": "propose_reusable_skill", "data": {"error": ValueError("private provider detail")}}

    monkeypatch.setattr(agent, "create_agent", lambda workspace="foundry": (FakeAgent(), None))
    coroutine = agent.stream_agent("create a reusable skill", [], emit, workspace="foundry")
    try:
        coroutine.send(None)
    except StopIteration as completed:
        result = completed.value
    else:
        raise AssertionError("Fake event stream unexpectedly suspended")

    assert result["reply"].startswith("I couldn’t prepare the draft")
    assert len([event for event in result["steps"] if event["type"] == "error"]) == 2
    assert not any("private provider detail" in str(event) for event in emitted)
    assert not any(event.get("name") == "propose_reusable_skill" and event.get("type") == "tool_end" for event in emitted)


def test_foundry_agent_returns_the_confirmed_draft_id(monkeypatch):
    draft_id = "a" * 32

    async def emit(_event):
        return None

    class FakeAgent:
        async def astream_events(self, *_args, **_kwargs):
            yield {"event": "on_tool_start", "run_id": "t1", "name": "propose_reusable_skill", "data": {"input": {"task": "Create a skill"}}}
            yield {"event": "on_tool_end", "run_id": "t1", "name": "propose_reusable_skill", "data": {"output": f"Draft created: Example (id {draft_id})."}}

    monkeypatch.setattr(agent, "create_agent", lambda workspace="foundry": (FakeAgent(), None))
    coroutine = agent.stream_agent("create a reusable skill", [], emit, workspace="foundry")
    try:
        coroutine.send(None)
    except StopIteration as completed:
        result = completed.value
    else:
        raise AssertionError("Fake event stream unexpectedly suspended")

    assert result["created_draft_id"] == draft_id
    assert result["reply"].startswith("Review the draft below")


def test_foundry_model_uses_configured_skill_proposal_token_ceiling(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("ASF_SKILL_DRAFT_MAX_TOKENS", "2345")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **options):
            captured["model_options"] = options

    deepagents = ModuleType("deepagents")
    deepagents.create_deep_agent = lambda **options: "fake-agent"
    langchain_openai = ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "deepagents", deepagents)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)

    _, error = agent.create_agent("foundry")
    assert error is None
    assert captured["model_options"]["max_tokens"] == 2345


