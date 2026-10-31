import json
import subprocess
import skills


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args):
        return None


def test_test_code_returns_inputs_value_and_captured_prints(monkeypatch):
    inputs = {"json_text": '{"ok": true}', "generate_schema": True}
    runner_output = {
        "result": {"valid": True, "warnings": [], "schema": {"type": "object"}},
        "user_stdout": "validated input\n",
        "user_stderr": "",
    }
    monkeypatch.setattr(skills.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(skills.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(runner_output), ""))
    monkeypatch.setattr(skills, "connect", _Connection)

    result = skills.test_code("def run(inputs): return {'valid': True}", inputs)

    assert result["success"]
    assert result["inputs"] == inputs
    assert result["return_value"]["valid"] is True
    assert result["stdout"] == "validated input\n"
    assert result["user_stderr"] == ""


def test_test_code_rejects_empty_source_without_docker(monkeypatch):
    monkeypatch.setattr(skills.shutil, "which", lambda _: None)
    result = skills.test_code("  ", {"a": 1})
    assert result["success"] is False
    assert result["error"] == "No Python source provided."
    assert result["exit_code"] is None


def test_test_code_rejects_main_or_print_only_source_without_docker(monkeypatch):
    monkeypatch.setattr(skills.shutil, "which", lambda _: None)
    result = skills.test_code("def main():\n    print('done')\n\nif __name__ == '__main__':\n    main()", {})
    assert result["success"] is False
    assert result["status"] == "failed"
    assert "run(inputs)" in result["error"]
    assert "main() or print-only" in result["error"]


def test_test_code_reports_missing_docker_and_hash(monkeypatch):
    code = "def run(inputs): return inputs"
    monkeypatch.setattr(skills.shutil, "which", lambda _: None)
    result = skills.test_code(code, {"x": 1})
    assert result["status"] == "sandbox_unavailable"
    assert result["success"] is False
    assert result["source_hash"]
    assert "Docker is not installed" in result["error"]


def test_test_code_reports_engine_unavailable_and_records_execution(monkeypatch, isolated_project):
    code = "def run(inputs): return inputs"
    monkeypatch.setattr(skills.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(skills.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "failed to connect to the Docker API"))
    result = skills.test_code(code, {"a": 2}, "json-validator")
    assert result["status"] == "sandbox_unavailable"
    assert "engine is not running" in result["error"]
    with skills.connect() as conn:
        execution = conn.execute("SELECT slug,success,error FROM executions").fetchone()
    assert execution["slug"] == "json-validator"
    assert execution["success"] == 0
    assert "engine is not running" in execution["error"]


def test_test_code_reports_runtime_exception_separately_from_container_failure(monkeypatch, isolated_project):
    runner_result = {"result": None, "user_stdout": "before crash\n", "user_stderr": "warning\n", "exception": "ValueError: invalid input"}
    monkeypatch.setattr(skills.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(skills.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, json.dumps(runner_result), ""))
    result = skills.test_code("def run(inputs):\n    raise ValueError()\n    return {'ok': True}", {"x": 1})
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["exit_code"] == 1
    assert result["stdout"] == "before crash\n"
    assert result["user_stderr"] == "warning\n"
    assert result["error"] == "ValueError: invalid input"


def test_test_code_does_not_treat_none_return_as_success(monkeypatch, isolated_project):
    output = {"result": None, "user_stdout": "printed but not returned\n", "user_stderr": ""}
    monkeypatch.setattr(skills.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(skills.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(output), ""))
    result = skills.test_code("def run(inputs):\n    print('printed but not returned')\n    return None", {})
    assert result["success"] is False
    assert "returned no result" in result["error"]
    assert result["stdout"] == "printed but not returned\n"


def test_test_code_reports_timeout_and_does_not_raise(monkeypatch, isolated_project):
    monkeypatch.setattr(skills.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(skills.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 15, output="partial")))
    result = skills.test_code("def run(inputs): return None", {})
    assert result["success"] is False
    assert result["status"] == "timeout"
    assert result["error"] == "Sandbox timed out after 15 seconds."
    assert result["duration_ms"] == 15000


def test_test_code_rejects_non_json_runner_output(monkeypatch, isolated_project):
    monkeypatch.setattr(skills.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(skills.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "not json", ""))
    result = skills.test_code("def run(inputs): return None", {})
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Sandbox returned an invalid result envelope."
    assert result["return_value"] is None


def test_test_code_uses_default_network_and_restricted_docker_command(monkeypatch, isolated_project):
    calls = {}
    monkeypatch.setattr(skills.shutil, "which", lambda _: "docker.exe")
    def run(args, **kwargs):
        calls.update(args=args, kwargs=kwargs)
        return subprocess.CompletedProcess(args, 0, json.dumps({"result": 1, "user_stdout": "", "user_stderr": ""}), "")
    monkeypatch.setattr(skills.subprocess, "run", run)
    result = skills.test_code("def run(inputs): return 1", {})
    command = calls["args"]
    assert result["success"] is True
    assert command[:3] == ["docker.exe", "run", "--rm"]
    for option in ("--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "32"):
        assert option in command
    assert "--network" not in command
    assert "none" not in command
    assert calls["kwargs"]["timeout"] == 15
    assert calls["kwargs"]["check"] is False


def test_approved_skill_files_include_only_approved_markdown(monkeypatch, tmp_path):
    approved = tmp_path / "approved"
    folder = approved / "json-validator"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: json-validator\n---\nValidate JSON.", encoding="utf-8")
    (folder / "helper.py").write_text("private helper", encoding="utf-8")
    monkeypatch.setattr(skills, "APPROVED", approved)
    monkeypatch.setattr(skills, "get_skill", lambda slug: {"status": "approved"})

    files = skills.approved_skill_files()

    assert list(files) == ["/skills/approved/json-validator/SKILL.md"]
    assert files["/skills/approved/json-validator/SKILL.md"]["encoding"] == "utf-8"
    assert files["/skills/approved/json-validator/SKILL.md"]["content"].endswith("Validate JSON.")
    assert skills.approved_skill_directories() == ["/skills/approved/"]


def test_skill_frontmatter_name_uses_agent_skills_identifier_format():
    document = skills.skill_doc({"name": "JSON Validator", "description": "Validate JSON."})

    assert document.startswith("---\nname: json-validator\n")
    assert skills.slugify("  JSON / Schema: Validator  ") == "json-schema-validator"
    assert skills.slugify("A" * 59 + " / B") == "a" * 59
