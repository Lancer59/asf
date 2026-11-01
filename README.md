# Agent Skill Foundry

A local-first capability-management app with two workspaces: Skill Foundry for creating and governing reusable skills, and Skill Runner for ordinary tasks and approved-skill execution. The workspaces have separate pages and in-memory conversations while sharing model configuration, logs, SQLite lifecycle records, sandbox, and the approved `SKILL.md` folder.

Technical documentation and change proposals are maintained in [`doc/`](doc/), including the [technical architecture](doc/technical_readme.md) and the [two-workspace change proposal](doc/change_proposal.md).

## Start on Windows

Requires Python 3.12 or newer (the app also works on 3.13) and a network connection for package installation and hosted providers.

```powershell
cd path\to\asf
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Configure the endpoint URL, API key, and model in the app Settings
python run.py
```

Open http://127.0.0.1:8000 for Skill Foundry, or go directly to http://127.0.0.1:8000/runner for Skill Runner. Foundry manages reusable capabilities; Runner handles general tasks and uses approved skills when relevant. Configure an OpenAI-compatible Chat Completions endpoint, API key, and model from Foundry Settings; both workspaces share the selected profile. Web search is optional and requires `TAVILY_API_KEY`. Never commit `.env`.

Settings can also switch the active provider to **Azure OpenAI (demo)**. The Azure endpoint, API key, API version, and deployment are stored separately from the normal OpenAI-compatible profile; switching back restores that profile. Azure uses `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, and `AZURE_OPENAI_DEPLOYMENT_NAME`. For newer Azure GPT deployments, the API version can be set to `v1`, which uses the Azure `/openai/v1` endpoint.

The chat header model picker supports multiple OpenAI-compatible and multiple Azure profiles. Use **Connection settings → Add profile** to add one of either type, enter its endpoint, model/deployment and key variable, then save; it appears in the chat picker immediately. You can also edit `ASF_MODELS` in `.env` as a JSON array. When `ASF_MODELS` is set, it is the complete picker list; the app does not add other profiles. For OpenAI-compatible entries use `provider_type: "openai_compatible"`, `base_url`, `model`, and `api_key_env`; for Azure use `provider_type: "azure_openai"`, `endpoint`, `api_version`, `model` (deployment name), and `api_key_env`. Each profile needs its own unique ID. Without `ASF_MODELS`, ASF falls back to the standard OpenAI-compatible and Azure environment settings. Keys stay in their separate environment variables and are never sent to the browser. Restart ASF after editing `.env` directly; changing the active profile takes effect on the next request and persists as `ASF_ACTIVE_MODEL`.

Open **Connection settings** to choose a profile and edit its label, provider type, endpoint, model/deployment, API version, and API key environment-variable name. Enter a new key only when you want to change it; leave the key field blank to keep the saved key. This also lets you fix a profile that points at Azure but was marked as a generic OpenAI-compatible endpoint.

If you use `uv`, `uv sync` creates `.venv`, then copy `.env.example` to `.env` and start with `uv run python run.py`. `requirements.in` is the human-maintained dependency input and `requirements.txt` is its pip-compatible install output. Regenerate it after package changes with `pip-compile requirements.in -o requirements.txt` when PyPI is reachable.

## Logs

ASF logs each HTTP request, agent run, tool start/finish, skill test outcome, and skill lifecycle change to the console and to `data/logs/asf.log`. Each log file rotates at 20 MiB and keeps five backup files. Open **Logs** in the chat header or visit `/logs` to inspect the logs. The dashboard defaults to the newest file and can download a standalone HTML snapshot with a notice that its contents are generated from logs. Every HTTP response includes an `X-Request-ID` header; use that value to find all related log lines. For debugging draft generation, logs now include the full Foundry system prompt, model request/response payloads, user prompt/history, proposal tool input/output, and detailed errors. These logs may contain user-provided secrets or generated source; protect log files and redact them before sharing. Provider/model tokens are tracked separately from these debug payloads.

The dashboard also summarizes HTTP request volume/failures/average duration and AI activity across the full selected file: agent runs and their outcomes, model calls and failures, provider-reported prompt/completion/total tokens, tool runs and failures, per-tool run/failure counts, and model calls without reported token usage. Token counts depend on what the configured OpenAI-compatible endpoint returns; missing counts are shown as unavailable rather than estimated. The table row limit does not truncate the summary metrics.

Set `LOG_LEVEL=DEBUG` in `.env` for more detailed event metadata. Set `ASF_LOG_FILE=` to disable the file log while keeping console logging. The default log directory is Git-ignored; if you choose a custom file path, keep it outside version control.

## Automated tests

Install the development dependencies and run the suite from the project root:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
```

The tests cover provider configuration, both workspace routes and isolated chat histories, role-specific tool boundaries, draft review and approval, skill versioning and audit records, agent skill selection and progress labels, and Python sandbox outcomes. Tests use temporary SQLite databases and mock provider and Docker calls, so the default suite does not need API credentials, a running model endpoint, web search, or Docker Engine. The default route checks exercise the SSE response contract without opening a local socket server.

Opt-in live tests send bounded requests through the configured agent and verify Foundry and Runner behaviors, including a greeting without tool calls and a real approved-skills tool call. They can incur provider usage charges:

```powershell
$env:ASF_LIVE_TESTS = "1"
python -m pytest -m live -q
```

In Windows Command Prompt (`cmd.exe`), use `set ASF_LIVE_TESTS=1` (no spaces around `=`), then run `python -m pytest -m live -q`.

The live test reads credentials from the process environment or `.env`; it is skipped unless explicitly enabled. Remove the flag afterward with `Remove-Item Env:ASF_LIVE_TESTS`.

## Skill lifecycle

Foundry produces a reviewable candidate with a request-specific name, purpose, agent instructions, input definitions, expected output, tests, and (for code skills) complete Python source. Complete proposal fields stream into the right-side draft workspace as they are generated. One Foundry conversation focuses on one skill; clear it to begin another. Instructions are included in the approved `SKILL.md` together with explicit JSON input and output contracts so Runner can select a skill and supply the right values. Skill identifiers use lowercase letters/digits separated by single hyphens; the library keeps a readable display name.

From a skill's detail page, **Edit with agent** opens a focused revision chat. The agent returns a proposed content change and a field-by-field diff without changing the saved skill. Review the proposal, then apply it to create a new version. Python/hybrid revisions are tested with the supplied test inputs before saving; a failed or unavailable test leaves the approved version unchanged.

Python/hybrid candidates must expose a synchronous `run(inputs)` function. It may call helper functions, but it must return a JSON-serializable result; `main()`-only and print-only code does not count as a skill implementation. In Foundry, review and edit the candidate, provide useful test inputs, run the sandbox test, inspect the returned value and captured prints separately, then explicitly accept it. Python acceptance requires a passing test of the current source. Approval writes a versioned `SKILL.md` directory and audit/version records to `data/skills.sqlite`. Editing an approved skill creates a new version; deprecated skills remain in the audit trail and are hidden from Runner. Arithmetic and other reusable tasks are capabilities to create and approve as skills; Runner has no special built-in calculator.

Neither workspace persists chat transcripts. Drafts and conversations live in process memory and disappear after a restart. Foundry keeps one skill per conversation; use Clear conversation to remove its unapproved draft and start another skill. Approved skills stay in the library. Runner clear only resets the Runner transcript.

The lifecycle API is rooted at `/api/skills`; interactive API documentation is at `/docs`. Chat clearing only clears the in-memory transcript. Refreshing the page starts a new conversation. The app does not write transcripts to SQLite, browser storage, or cookies.

## Python skills and isolation

Python skill execution is configured with `ASF_SANDBOX_BACKEND` in `.env`. Set it to `docker` (default) to run in a constrained container on Docker's default network, or `local` to run in a separate Python process on your Windows laptop. Local mode can use installed Windows programs such as PowerShell and Windows APIs, and Python skills can make outbound API calls. It is not isolated: skill code has the file, program, and network permissions of your Windows account. Use local mode only for code you trust. Restart ASF after changing the setting. Both modes enforce a 15-second timeout; Docker also applies container resource and filesystem restrictions. Skill dependencies are not installed automatically, so the selected Python environment must already provide any imported packages.

## Layout

```text
asf/
  app.py, agent.py, skills.py, run.py
  templates/index.html       # Skill Foundry
  templates/runner.html      # Skill Runner
  static/app.js, runner.js, styles.css
  skills/approved/<slug>/SKILL.md
  data/skills.sqlite
  doc/technical_readme.md, change_proposal.md
```

## Current scope and evaluation

The prototype includes version lineage, governance events, approved-skill retrieval by metadata/text, execution records and test source hashes. Retrieval is deliberately local and lexical/metadata-based in this first runnable build; no hosted vector database or embedding service is included. The app does not claim that a generated capability is correct because it parses or because an agent proposed it. Review and passing validation are required before promotion.

The research comparison should run the same fixed task set against (A) the baseline model without approved ASF skills and (B) ASF with selected approved skills. Record provider/model, task rubric, retrieval relevance, task success, model/tool usage where exposed, wall-clock latency, validation effort, skill-version regression outcomes and tool failures. Keep the task set and scoring rubric with the eventual evaluation report; no performance improvement is claimed until this is measured.

## Technology references

- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview) and [skills format](https://docs.langchain.com/oss/python/deepagents/skills)
- [LangChain Tavily Search](https://docs.langchain.com/oss/python/integrations/tools/tavily_search)
- [LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)
