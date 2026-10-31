# Agent Skill Foundry

A local-first capability-management prototype with one Deep Agents conversation and a separate skill lifecycle library. The app keeps the current conversation in process memory only; SQLite and Deep Agents-compatible `SKILL.md` folders persist approved skill versions, audit events, validation output and execution outcomes.

## Start on Windows

Requires Python 3.12 or newer (the app also works on 3.13) and a network connection for package installation and hosted providers.

```powershell
cd path\to\agent-skill-foundry\new
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Configure the endpoint URL, API key, and model in the app Settings
python run.py
```

Open http://127.0.0.1:8000. The Skills library and chat UI work without credentials. Configure any OpenAI-compatible Chat Completions endpoint, API key, and model from Settings; credentials are stored in the local `.env`. Web search is optional and requires `TAVILY_API_KEY`. Never commit `.env`.

If you use `uv`, `uv sync` creates `.venv`, then copy `.env.example` to `.env` and start with `uv run python run.py`. `requirements.in` is the human-maintained dependency input and `requirements.txt` is its pip-compatible install output. Regenerate it after package changes with `pip-compile requirements.in -o requirements.txt` when PyPI is reachable.

## Automated tests

Install the development dependencies and run the suite from the project root:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
```

The tests cover provider configuration, chat and skill API behavior, draft review and approval, skill versioning and audit records, agent skill selection and progress labels, and Python sandbox outcomes. Tests use temporary SQLite databases and mock provider and Docker calls, so the default suite does not need API credentials, a running model endpoint, web search, or Docker Engine. The default route checks exercise the SSE response contract without opening a local socket server.

An opt-in live test starts the local app, sends a real request to the configured model, and verifies that the model calls the approved-skills tool and completes the SSE response. This also exercises actual async event streaming through HTTP. It can incur provider usage charges:

```powershell
$env:ASF_LIVE_TESTS = "1"
python -m pytest -m live -q
```

In Windows Command Prompt (`cmd.exe`), use `set ASF_LIVE_TESTS=1` (no spaces around `=`), then run `python -m pytest -m live -q`.

The live test reads credentials from the process environment or `.env`; it is skipped unless explicitly enabled. Remove the flag afterward with `Remove-Item Env:ASF_LIVE_TESTS`.

## Skill lifecycle

Chat can use the live Tavily web search integration, use approved Deep Agent skills, or produce a draft for review. Skills describe reusable capabilities; city, URL, date, topic and similar request data belong in inputs. Drafts live in process memory and disappear after a restart. Edit the fields and complete Python source, run tests, inspect test output and use explicit acceptance to promote a passing draft. Approval writes a versioned `SKILL.md` directory and an audit/version record to `data/skills.sqlite`. Editing an approved skill creates a new version. Deprecated skills remain in the audit trail and are withheld from the agent.

The lifecycle API is rooted at `/api/skills`; interactive API documentation is at `/docs`. Chat clearing only clears the in-memory transcript. Refreshing the page starts a new conversation. The app does not write transcripts to SQLite, browser storage, or cookies.

## Python skills and isolation

Docker Desktop/Engine is required to test or run reviewed Python skill code. Every run uses a disposable Docker container with no network, a read-only source mount, memory/CPU/process limits, dropped Linux capabilities, no new privileges and a hard timeout. If Docker is missing, tests report that execution is disabled. The app never runs generated Python in its host process and never installs draft dependencies; generated skill dependencies are metadata only. Python code that needs external packages must use a separately reviewed/prebuilt worker image (the v1 runner intentionally ships only the Python base image).

## Layout

```text
new/
  app.py, agent.py, skills.py, run.py
  templates/index.html
  static/app.js, styles.css
  skills/approved/<slug>/SKILL.md
  data/skills.sqlite
```

## Current scope and evaluation

The prototype includes version lineage, governance events, approved-skill retrieval by metadata/text, execution records and test source hashes. Retrieval is deliberately local and lexical/metadata-based in this first runnable build; no hosted vector database or embedding service is included. The app does not claim that a generated capability is correct because it parses or because an agent proposed it. Review and passing validation are required before promotion.

The research comparison should run the same fixed task set against (A) the baseline model without approved ASF skills and (B) ASF with selected approved skills. Record provider/model, task rubric, retrieval relevance, task success, model/tool usage where exposed, wall-clock latency, validation effort, skill-version regression outcomes and tool failures. Keep the task set and scoring rubric with the eventual evaluation report; no performance improvement is claimed until this is measured.

## Technology references

- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview) and [skills format](https://docs.langchain.com/oss/python/deepagents/skills)
- [LangChain Tavily Search](https://docs.langchain.com/oss/python/integrations/tools/tavily_search)
- [LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)
