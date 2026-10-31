import json

import skills


def _candidate(name="JSON Validator", skill_type="instruction"):
    return {
        "name": name,
        "description": "Validate JSON documents.",
        "skill_type": skill_type,
        "instructions": "Parse the supplied document and report the result.",
        "code": "" if skill_type == "instruction" else "def run(inputs): return {'valid': True}",
        "inputs": {"json_text": {"type": "string"}},
        "expected_output": "A validation report.",
        "dependencies": [], "permissions": [], "tags": ["json", "validation"],
        "category": "data validation", "tests": [], "tools": [],
    }


def test_proposal_defaults_are_complete_and_requests_are_preserved(isolated_project):
    task = "Create a reusable skill to validate configuration files"
    draft = skills.propose_skill(task)
    assert draft["request"] == task
    assert draft["skill"]["skill_type"] == "instruction"
    assert draft["skill"]["tests"][0]["inputs"]["user_input"] == task
    assert draft["attempts"] == []


def test_proposal_name_is_derived_from_request_instead_of_generic_placeholder(isolated_project):
    draft = skills.propose_skill("Create a reusable skill to validate JSON documents")
    assert draft["skill"]["name"] == "JSON Document Validator"
    draft = skills.propose_skill("I want a JSON validation skill please", {"name": "New reusable capability"})
    assert draft["skill"]["name"] == "JSON Validation"


def test_instruction_promotion_persists_searchable_record_version_and_spec_doc(isolated_project):
    draft = skills.propose_skill("Validate JSON")
    draft["skill"] = _candidate()
    result = skills.promote(draft)

    assert result["status"] == "approved"
    item = skills.get_skill("json-validator")
    assert item["version"] == 1
    assert item["content"]["name"] == "JSON Validator"
    assert item["metadata"]["tags"] == ["json", "validation"]
    assert item["history"][0]["note"] == "Approved reviewed candidate"
    assert item["audit"][0]["action"] == "approved"
    doc = (skills.APPROVED / "json-validator" / "SKILL.md").read_text(encoding="utf-8")
    assert doc.startswith("---\nname: json-validator\ndescription:")
    assert "Parse the supplied document" in doc
    assert "## Agent instructions" in doc
    assert "## Input contract" in doc and '"json_text"' in doc
    assert "## Output contract" in doc
    assert skills.list_skills(q="validation", status="approved")[0]["slug"] == "json-validator"
    assert skills.approved_skill_files()["/skills/approved/json-validator/SKILL.md"]["content"] == doc


def test_repromotion_increments_version_and_preserves_creation_time(isolated_project):
    first = skills.propose_skill("first")
    first["skill"] = _candidate()
    original = skills.promote(first)["skill"]
    second = skills.propose_skill("second")
    second["skill"] = {**_candidate(), "description": "Updated description."}
    promoted = skills.promote(second)["skill"]

    assert promoted["version"] == 2
    assert promoted["created_at"] == original["created_at"]
    assert [entry["version"] for entry in promoted["history"]] == [2, 1]
    assert promoted["description"] == "Updated description."


def test_python_promotion_refuses_failed_sandbox_execution(isolated_project, monkeypatch):
    draft = skills.propose_skill("Implement a checker")
    draft["skill"] = _candidate("Python Checker", "python")
    monkeypatch.setattr(skills, "test_code", lambda *args, **kwargs: {"success": False, "status": "failed", "error": "bad test"})

    result = skills.promote(draft)

    assert result["status"] == "test_failed"
    assert not (skills.APPROVED / "python-checker").exists()
    assert skills.get_skill("python-checker") is None


def test_python_promotion_records_code_file_when_test_passes(isolated_project, monkeypatch):
    draft = skills.propose_skill("Implement a checker")
    draft["skill"] = _candidate("Python Checker", "python")
    monkeypatch.setattr(skills, "test_code", lambda *args, **kwargs: {"success": True, "status": "passed"})

    result = skills.promote(draft)

    assert result["status"] == "approved"
    assert (skills.APPROVED / "python-checker" / "skill.py").read_text(encoding="utf-8") == draft["skill"]["code"]
    doc = (skills.APPROVED / "python-checker" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Agent instructions" in doc
    assert "## Input contract" in doc and '"json_text"' in doc
    assert "## Output contract" in doc
    assert "run(inputs: dict)" in doc


def test_update_requires_passing_python_test_and_versions_changes(isolated_project, monkeypatch):
    draft = skills.propose_skill("seed")
    draft["skill"] = _candidate()
    skills.promote(draft)
    monkeypatch.setattr(skills, "test_code", lambda *args, **kwargs: {"success": False, "status": "failed"})
    failed = skills.update_skill("json-validator", {"skill_type": "python", "code": "bad code"})
    assert failed["status"] == "test_failed"
    monkeypatch.setattr(skills, "test_code", lambda *args, **kwargs: {"success": True, "status": "passed"})
    changed = skills.update_skill("json-validator", {"skill_type": "python", "code": "def run(inputs): return True"})
    assert changed["status"] == "approved"
    assert changed["skill"]["version"] == 2
    assert skills.update_skill("does-not-exist", {}) is None


def test_update_uses_revision_test_inputs_for_validation_before_versioning(isolated_project, monkeypatch):
    candidate = _candidate(skill_type="python")
    candidate["tests"] = [{"name": "custom input", "inputs": {"json_text": "{}"}, "expected": {"valid": True}}]
    draft = skills.propose_skill("seed", candidate)
    monkeypatch.setattr(skills, "test_code", lambda *args, **kwargs: {"success": True, "status": "passed"})
    skills.promote(draft)

    seen = []
    def passing_test(_code, inputs, *_args, **_kwargs):
        seen.append(inputs)
        return {"success": True, "status": "passed"}

    monkeypatch.setattr(skills, "test_code", passing_test)
    revision_inputs = {"json_text": '{"active":true}'}
    revised = skills.update_skill(
        "json-validator", {"code": "def run(inputs): return {'valid': True}"}, inputs=revision_inputs,
    )

    assert revised["status"] == "approved"
    assert revised["skill"]["version"] == 2
    assert seen == [revision_inputs]


def test_deprecate_changes_status_and_audits(isolated_project):
    draft = skills.propose_skill("seed")
    draft["skill"] = _candidate()
    skills.promote(draft)
    deprecated = skills.deprecate("json-validator")
    assert deprecated["status"] == "deprecated"
    assert skills.list_skills(status="approved") == []
    assert skills.list_skills(status="deprecated")[0]["slug"] == "json-validator"
    assert deprecated["audit"][0]["action"] == "deprecated"
    assert skills.deprecate("missing") is None


def test_audit_stores_json_detail(isolated_project):
    skills.audit(None, "draft_rejected", {"draft_id": "abc", "reason": "user"})
    with skills.connect() as conn:
        row = conn.execute("SELECT action,detail FROM audit").fetchone()
    assert row["action"] == "draft_rejected"
    assert json.loads(row["detail"]) == {"draft_id": "abc", "reason": "user"}
