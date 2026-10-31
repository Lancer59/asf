import sys
from pathlib import Path

import pytest

# Keep imports stable when pytest is launched through different Windows entry
# points (the console script and ``python -m pytest`` set sys.path differently).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
import skills


@pytest.fixture(autouse=True)
def default_model_provider(monkeypatch):
    monkeypatch.setenv("ASF_LLM_PROVIDER", "openai_compatible")
    for key in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT_NAME", "ASF_MODELS", "ASF_ACTIVE_MODEL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    """Use a disposable SQLite database and approved-skill directory."""
    monkeypatch.setenv("ASF_LLM_PROVIDER", "openai_compatible")
    for key in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(skills, "DB_PATH", tmp_path / "data" / "skills.sqlite")
    monkeypatch.setattr(skills, "APPROVED", tmp_path / "skills" / "approved")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    skills.DRAFTS.clear()
    app.FOUNDRY_MESSAGES.clear()
    app.RUNNER_MESSAGES.clear()
    app.FOUNDRY_ACTIVE_DRAFT_ID = None
    app.FOUNDRY_SESSION_SKILL_NAME = None
    app.FOUNDRY_PENDING_CLARIFICATION = False
    yield tmp_path
    skills.DRAFTS.clear()
    app.FOUNDRY_MESSAGES.clear()
    app.RUNNER_MESSAGES.clear()
    app.FOUNDRY_ACTIVE_DRAFT_ID = None
    app.FOUNDRY_SESSION_SKILL_NAME = None
    app.FOUNDRY_PENDING_CLARIFICATION = False


@pytest.fixture
def client(isolated_project):
    from dataclasses import dataclass
    from urllib.parse import parse_qs, urlsplit
    from fastapi import HTTPException
    from pydantic import ValidationError

    @dataclass
    class Response:
        status_code: int
        data: object

        def json(self):
            return self.data

        @property
        def text(self):
            return self.data

    class EndpointClient:
        """Call endpoint functions directly; avoid Windows socket-based clients."""
        def request(self, method, url, **kwargs):
            parts = urlsplit(url)
            path = parts.path
            body = kwargs.get("json", {})
            try:
                if method == "GET" and path == "/":
                    from starlette.requests import Request
                    request = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http", "client": ("test", 1), "root_path": ""})
                    result = app.home(request).body.decode()
                elif method == "GET" and path == "/foundry":
                    from starlette.requests import Request
                    request = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http", "client": ("test", 1), "root_path": ""})
                    result = app.foundry_home(request).body.decode()
                elif method == "GET" and path == "/runner":
                    from starlette.requests import Request
                    request = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http", "client": ("test", 1), "root_path": ""})
                    result = app.runner_home(request).body.decode()
                elif method == "GET" and path == "/api/config":
                    result = app.configuration()
                elif method == "GET" and path == "/api/skills":
                    query = parse_qs(parts.query)
                    result = app.list_skills(query.get("q", [""])[0], query.get("status", [""])[0])
                elif method == "GET" and path.startswith("/api/skills/drafts/"):
                    result = app.get_draft(path.rsplit("/", 1)[1])
                elif method == "GET" and path.startswith("/api/skills/"):
                    result = app.get_skill(path.rsplit("/", 1)[1])
                elif method == "PUT" and path == "/api/config/provider":
                    result = app.save_provider_config(app.ProviderConfig.model_validate(body))
                elif method == "PUT" and path == "/api/config/model":
                    result = app.select_model(app.ModelSelection.model_validate(body))
                elif method == "PUT" and path == "/api/config/model-profile":
                    result = app.save_model_profile(app.ModelProfileConfig.model_validate(body))
                elif method == "POST" and path == "/api/config/model-profile":
                    result = app.create_model_profile(app.ModelProfileConfig.model_validate(body))
                elif method == "POST" and path == "/api/chat/stream":
                    result = app.chat_stream(app.ChatInput.model_validate(body))
                elif method == "POST" and path == "/api/foundry/chat/stream":
                    result = app.foundry_chat_stream(app.ChatInput.model_validate(body))
                elif method == "POST" and path == "/api/runner/chat/stream":
                    result = app.runner_chat_stream(app.ChatInput.model_validate(body))
                elif method == "POST" and path == "/api/chat/clear":
                    result = app.clear_chat()
                elif method == "POST" and path == "/api/foundry/chat/clear":
                    result = app.clear_foundry_chat()
                elif method == "POST" and path == "/api/runner/chat/clear":
                    result = app.clear_runner_chat()
                elif method == "POST" and path == "/api/skills/drafts":
                    result = app.create_draft(app.ChatInput.model_validate(body))
                elif method == "POST" and "/test" in path and "/drafts/" in path:
                    result = app.test_draft(path.split("/")[4], app.DraftInput.model_validate(body))
                elif method == "POST" and path.endswith("/review"):
                    result = app.review_draft(path.split("/")[4], app.ReviewInput.model_validate(body))
                elif method == "POST" and path.endswith("/revise"):
                    result = app.revise_draft(path.split("/")[4], app.ReviewInput.model_validate(body))
                elif method == "POST" and path.endswith("/agent-revision"):
                    result = app.revise_approved_skill(path.split("/")[3], app.AgentRevisionInput.model_validate(body))
                elif method == "POST" and path.endswith("/deprecate"):
                    result = app.deprecate_skill(path.split("/")[3])
                elif method == "POST" and path.startswith("/api/skills/") and path.endswith("/test"):
                    result = app.test_skill(path.split("/")[3], app.DraftInput.model_validate(body))
                elif method == "PUT" and "/drafts/" in path:
                    result = app.edit_draft(path.split("/")[4], app.DraftInput.model_validate(body))
                elif method == "PATCH" and path.startswith("/api/skills/"):
                    result = app.patch_skill(path.split("/")[3], app.EditInput.model_validate(body))
                else:
                    raise AssertionError(f"Unmapped test endpoint: {method} {url}")
                return Response(200, result)
            except HTTPException as exc:
                return Response(exc.status_code, {"detail": exc.detail})
            except ValidationError as exc:
                return Response(422, {"detail": exc.errors()})

        def get(self, url, **kwargs): return self.request("GET", url, **kwargs)
        def post(self, url, **kwargs): return self.request("POST", url, **kwargs)
        def put(self, url, **kwargs): return self.request("PUT", url, **kwargs)
        def patch(self, url, **kwargs): return self.request("PATCH", url, **kwargs)

    return EndpointClient()
