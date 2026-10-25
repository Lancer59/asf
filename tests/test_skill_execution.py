import json
import subprocess
from pathlib import Path

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
