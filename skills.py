"""Skill lifecycle, SQLite audit, retrieval, and isolated code execution."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "skills.sqlite"
APPROVED = ROOT / "skills" / "approved"
DRAFTS: dict[str, dict] = {}
MAX_OUTPUT = 20000
logger = logging.getLogger("asf.skills")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS skills (
        slug TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
        skill_type TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
        content TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, last_test TEXT, last_execution TEXT
      );
      CREATE TABLE IF NOT EXISTS versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL, version INTEGER NOT NULL,
        content TEXT NOT NULL, metadata TEXT NOT NULL, changed_at TEXT NOT NULL,
        actor TEXT NOT NULL, note TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, action TEXT NOT NULL,
        detail TEXT NOT NULL, created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, source_hash TEXT,
        success INTEGER NOT NULL, output TEXT, error TEXT, duration_ms INTEGER NOT NULL,
        created_at TEXT NOT NULL
      );
    """)
    return conn


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())[:60].strip("-")
    return slug or "skill-" + uuid.uuid4().hex[:8]


def skill_doc(skill: dict) -> str:
    # Agent Skills frontmatter names are identifiers, while ASF keeps the
    # original human-friendly name in its library and UI.
    front = {"name": slugify(skill["name"]), "description": skill["description"]}
    return "---\n" + yaml.safe_dump(front, sort_keys=False).strip() + "\n---\n\n" + (skill.get("instructions") or "") + "\n"


def _record(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"])
    d["content"] = json.loads(d["content"])
    return d


def list_skills(q: str = "", status: str = "") -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM skills ORDER BY updated_at DESC").fetchall()
    records = [_record(r) for r in rows]
    query = q.strip().lower()
    return [r for r in records if (not status or r["status"] == status) and
            (not query or query in (r["name"] + " " + r["description"] + " " + " ".join(r["metadata"].get("tags", []))).lower())]


def get_skill(slug: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE slug=?", (slug,)).fetchone()
        if not row:
            return None
        result = _record(row)
        result["history"] = [dict(v) for v in conn.execute("SELECT version,changed_at,actor,note FROM versions WHERE slug=? ORDER BY version DESC", (slug,))]
        result["audit"] = [dict(a) for a in conn.execute("SELECT action,detail,created_at FROM audit WHERE slug=? ORDER BY id DESC LIMIT 30", (slug,))]
        return result


def audit(slug: str | None, action: str, detail: dict) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit(slug,action,detail,created_at) VALUES(?,?,?,?)", (slug, action, json.dumps(detail), now()))
    logger.info("skill_audit_recorded action=%s slug=%s", action, slug or "-")


def propose_skill(task: str, candidate: dict | None = None) -> dict:
    skill = dict(candidate) if candidate else {
        "name": "New reusable capability", "description": "A reusable capability proposed from the current task.",
        "skill_type": "instruction", "instructions": f"\nDefine a repeatable process for: {task}\nKeep request-specific values as inputs.",
        "code": "", "inputs": {"user_input": {"type": "string", "default": ""}}, "expected_output": "A useful result for the supplied input.",
        "dependencies": [], "permissions": [], "tags": [], "category": "general",
        "tests": [{"name": "Original task", "inputs": {"user_input": task}, "expected": ""}], "tools": []
    }
    skill.setdefault("name", "New reusable capability")
    skill.setdefault("description", f"A reusable capability for: {task}")
    skill.setdefault("skill_type", "instruction")
    skill.setdefault("code", "")
    skill.setdefault("instructions", "")
    skill.setdefault("tests", [])
    skill.setdefault("inputs", {})
    draft_id = uuid.uuid4().hex
    DRAFTS[draft_id] = {"id": draft_id, "request": task, "skill": skill, "attempts": []}
    logger.info("skill_draft_created draft_id=%s skill_type=%s request_chars=%s", draft_id, skill.get("skill_type"), len(task))
    return DRAFTS[draft_id]


def test_code(code: str, inputs: dict, slug: str | None = None) -> dict:
    """Run user-reviewed Python in Docker only; never execute in this process."""
    if not code.strip():
        logger.warning("skill_test_rejected slug=%s reason=empty_source", slug or "-")
        return {"success": False, "error": "No Python source provided.", "stdout": "", "stderr": "", "exit_code": None, "duration_ms": 0, "source_hash": ""}
    docker = shutil.which("docker")
    if not docker:
        source_hash = hashlib.sha256(code.encode()).hexdigest()
        logger.warning("skill_test_unavailable slug=%s reason=docker_not_installed source_sha256=%s", slug or "-", source_hash)
        return {"success": False, "status": "sandbox_unavailable", "error": "Docker is not installed. Install and start Docker Desktop to run Python skill tests; skill code is never executed directly on the host.", "stdout": "", "stderr": "", "exit_code": None, "duration_ms": 0, "source_hash": source_hash}
    source_hash = hashlib.sha256(code.encode()).hexdigest()
    start = time.monotonic()
    logger.info("skill_test_started slug=%s source_chars=%s source_sha256=%s", slug or "-", len(code), source_hash)
    sandbox_root = ROOT / "data" / "sandbox"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asf-skill-", dir=sandbox_root) as folder:
        work = Path(folder)
        (work / "skill.py").write_text(code, encoding="utf-8")
        (work / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
        runner = """import contextlib, io, json, runpy
ns = runpy.run_path('/work/skill.py')
inputs = json.load(open('/work/inputs.json'))
captured_out, captured_err = io.StringIO(), io.StringIO()
try:
    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        value = ns.get('run', lambda data: ns.get('result'))(inputs)
    envelope = {'result': value, 'user_stdout': captured_out.getvalue(), 'user_stderr': captured_err.getvalue()}
except Exception as exc:
    envelope = {'result': None, 'user_stdout': captured_out.getvalue(), 'user_stderr': captured_err.getvalue(), 'exception': f'{type(exc).__name__}: {exc}'}
print(json.dumps(envelope, default=str))"""
        command = [docker, "run", "--rm", "--network", "none", "--read-only", "--user", "65534:65534",
                   "--memory", "128m", "--cpus", "0.5", "--pids-limit", "32", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                   "--mount", f"type=bind,src={work.resolve()},dst=/work,readonly", "python:3.12-alpine", "python", "-I", "-c", runner]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
            stdout, stderr, code_exit = proc.stdout[:MAX_OUTPUT], proc.stderr[:MAX_OUTPUT], proc.returncode
            lower_error = stderr.lower()
            engine_unavailable = code_exit != 0 and ("docker api" in lower_error or "dockerdesktoplinuxengine" in lower_error or "cannot connect to the docker daemon" in lower_error)
            returned = None
            user_stdout = user_stderr = ""
            runtime_error = None
            if code_exit == 0:
                try:
                    envelope = json.loads(stdout)
                    returned = envelope.get("result")
                    user_stdout = envelope.get("user_stdout", "")
                    user_stderr = envelope.get("user_stderr", "")
                    runtime_error = envelope.get("exception")
                    stdout = user_stdout
                    if runtime_error:
                        code_exit = 1
                        stderr = runtime_error
                except (json.JSONDecodeError, AttributeError):
                    returned = None
                    code_exit = 1
                    stderr = "Sandbox returned an invalid result envelope."
            result = {"success": code_exit == 0, "status": "passed" if code_exit == 0 else "sandbox_unavailable" if engine_unavailable else "failed",
                      "inputs": inputs, "return_value": returned, "stdout": stdout, "stderr": stderr,
                      "user_stderr": user_stderr, "exit_code": code_exit,
                      "error": None if code_exit == 0 else ("The Docker command is installed, but its engine is not running. Start Docker Desktop, wait until its engine is ready, then run the test again. The skill code was not executed." if engine_unavailable else (stderr or f"Sandbox exited with status {code_exit}")),
                      "duration_ms": int((time.monotonic()-start)*1000), "source_hash": source_hash}
        except subprocess.TimeoutExpired as exc:
            result = {"success": False, "status": "timeout", "stdout": (exc.stdout or "")[:MAX_OUTPUT], "stderr": (exc.stderr or "")[:MAX_OUTPUT],
                      "exit_code": None, "error": "Sandbox timed out after 15 seconds.", "duration_ms": 15000, "source_hash": source_hash}
    with connect() as conn:
        conn.execute("INSERT INTO executions(slug,source_hash,success,output,error,duration_ms,created_at) VALUES(?,?,?,?,?,?,?)",
                     (slug, source_hash, int(result["success"]), result["stdout"], result["error"], result["duration_ms"], now()))
        if slug:
            conn.execute("UPDATE skills SET last_test=?,last_execution=? WHERE slug=?", (json.dumps(result), "passed" if result["success"] else "failed", slug))
    log_method = logger.info if result["success"] else logger.warning
    log_method(
        "skill_test_finished slug=%s status=%s success=%s duration_ms=%s source_sha256=%s",
        slug or "-", result.get("status", "failed"), result["success"], result["duration_ms"], source_hash,
    )
    return result


def promote(draft: dict, actor: str = "user") -> dict:
    skill = draft["skill"]
    if skill.get("skill_type") in {"python", "hybrid"}:
        run = test_code(skill.get("code", ""), draft.get("accept_inputs", {"user_input": draft["request"]}))
        if not run["success"]:
            logger.warning("skill_promotion_blocked slug=%s reason=python_test_failed", slugify(skill.get("name", "skill")))
            return {"status": "test_failed", "test": run}
    slug = slugify(skill["name"])
    content = json.dumps(skill)
    metadata = json.dumps({k: skill.get(k) for k in ("inputs", "expected_output", "dependencies", "permissions", "tags", "category", "tests", "tools")})
    doc = skill_doc(skill)
    folder = APPROVED / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(doc, encoding="utf-8")
    if skill.get("code"):
        (folder / "skill.py").write_text(skill["code"], encoding="utf-8")
    with connect() as conn:
        previous = conn.execute("SELECT * FROM skills WHERE slug=?", (slug,)).fetchone()
        version = (previous["version"] + 1) if previous else 1
        created = previous["created_at"] if previous else now()
        conn.execute("INSERT OR REPLACE INTO skills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (slug, skill["name"], skill["description"], skill["skill_type"], "approved", version, content, metadata, created, now(), None, "pending"))
        conn.execute("INSERT INTO versions(slug,version,content,metadata,changed_at,actor,note) VALUES(?,?,?,?,?,?,?)", (slug, version, content, metadata, now(), actor, "Approved reviewed candidate"))
        conn.execute("INSERT INTO audit(slug,action,detail,created_at) VALUES(?,?,?,?)", (slug, "approved", json.dumps({"version": version, "source_hash": hashlib.sha256(skill.get("code", "").encode()).hexdigest()}), now()))
    logger.info("skill_approved slug=%s version=%s skill_type=%s", slug, version, skill.get("skill_type"))
    return {"status": "approved", "skill": get_skill(slug)}


def update_skill(slug: str, content: dict, actor: str = "user") -> dict:
    old = get_skill(slug)
    if not old:
        return None
    skill = {**old["content"], **content}
    draft = {"skill": skill, "request": "revision", "accept_inputs": {"user_input": "revision regression"}}
    if skill.get("skill_type") in {"python", "hybrid"}:
        run = test_code(skill.get("code", ""), draft["accept_inputs"])
        if not run["success"]:
            logger.warning("skill_update_blocked slug=%s reason=python_test_failed", slug)
            return {"status": "test_failed", "test": run}
    return promote(draft, actor)


def deprecate(slug: str) -> dict | None:
    with connect() as conn:
        cur = conn.execute("UPDATE skills SET status='deprecated',updated_at=? WHERE slug=?", (now(), slug))
        if not cur.rowcount:
            logger.warning("skill_deprecation_not_found slug=%s", slug)
            return None
        conn.execute("INSERT INTO audit(slug,action,detail,created_at) VALUES(?,?,?,?)", (slug, "deprecated", "{}", now()))
    logger.info("skill_deprecated slug=%s", slug)
    return get_skill(slug)


def approved_skill_directories() -> list[str]:
    return ["/skills/approved/"] if approved_skill_files() else []


def approved_skill_files() -> dict[str, str]:
    """Expose only approved SKILL.md instructions through the virtual backend."""
    files: dict[str, str] = {}
    for skill_md in APPROVED.glob("*/SKILL.md"):
        record = get_skill(skill_md.parent.name)
        if record and record["status"] == "approved":
            virtual_path = "/skills/approved/" + skill_md.parent.name + "/SKILL.md"
            files[virtual_path] = {"content": skill_md.read_text(encoding="utf-8"), "encoding": "utf-8"}
    return files
