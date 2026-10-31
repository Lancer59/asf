"""Opt-in real-provider smoke test for ASF agent tool calling."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

import agent
import llm_provider

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.live


def _provider_ready() -> bool:
    load_dotenv(ROOT / ".env", override=False)
    return llm_provider.provider_configured()


def test_live_model_calls_approved_skill_tool_and_returns_tool_result():
    if os.getenv("ASF_LIVE_TESTS") != "1":
        pytest.skip("Set ASF_LIVE_TESTS=1 to enable provider-backed integration tests")
    if not _provider_ready():
        pytest.skip("Configure an OpenAI-compatible endpoint and API key in the environment or .env")

    try:
        result = agent.invoke_agent(
            "Call the list_approved_skills tool before answering. Then say exactly: live tool call verified.",
            [],
        )
    except Exception as exc:
        pytest.fail(f"Live model request failed: {type(exc).__name__}: {exc}")

    assert not result.get("error"), result
    steps = result.get("steps", [])
    calls = [call for step in steps for call in step.get("tool_calls", [])]
    assert any(call.get("name") == "list_approved_skills" for call in calls), steps
    assert any(step.get("role") == "tool" for step in steps), steps
    assert result.get("reply", "").strip(), result


def test_live_foundry_greeting_does_not_call_tools():
    if os.getenv("ASF_LIVE_TESTS") != "1":
        pytest.skip("Set ASF_LIVE_TESTS=1 to enable provider-backed integration tests")
    if not _provider_ready():
        pytest.skip("Configure an OpenAI-compatible endpoint and API key in the environment or .env")

    result = agent.invoke_agent("Hi", [], workspace="foundry")
    calls = [call for step in result.get("steps", []) for call in step.get("tool_calls", [])]
    assert not result.get("error"), result
    assert not calls, calls
    assert result.get("reply", "").strip(), result
