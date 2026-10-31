"""Environment-driven model profiles shared by chat and skill-revision calls."""
from __future__ import annotations

import json
import os
import re

OPENAI_COMPATIBLE = "openai_compatible"
AZURE_OPENAI = "azure_openai"
DEFAULT_MODEL_ID = "openai-compatible"
AZURE_MODEL_ID = "azure-openai"
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _key_env(profile: dict) -> str:
    default = "AZURE_OPENAI_API_KEY" if profile.get("provider_type") == AZURE_OPENAI else "OPENAI_API_KEY"
    candidate = str(profile.get("api_key_env", default))
    return candidate if _ENV_RE.fullmatch(candidate) else default


def _legacy_profiles() -> list[dict]:
    return [
        {
            "id": DEFAULT_MODEL_ID,
            "label": os.getenv("ASF_OPENAI_MODEL_LABEL", "OpenAI-compatible · " + os.getenv("LLM_MODEL", "gpt-4o-mini")),
            "provider_type": OPENAI_COMPATIBLE,
            "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
            "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.openai.com/v1"),
            "api_key_env": "OPENAI_API_KEY",
            "legacy": True,
        },
        {
            "id": AZURE_MODEL_ID,
            "label": os.getenv("ASF_AZURE_MODEL_LABEL", "Azure OpenAI · " + os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.3-chat")),
            "provider_type": AZURE_OPENAI,
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.3-chat"),
            "base_url": os.getenv("AZURE_OPENAI_ENDPOINT", "https://servicedeskgpt.openai.azure.com/"),
            "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            "api_key_env": "AZURE_OPENAI_API_KEY",
            "legacy": True,
        },
    ]


def model_profiles() -> list[dict]:
    """Use ASF_MODELS as the complete catalog; otherwise use legacy env profiles."""
    raw = os.getenv("ASF_MODELS", "").strip()
    if not raw:
        return _legacy_profiles()
    if len(raw) > 32768:
        return _legacy_profiles()
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        return _legacy_profiles()
    if not isinstance(configured, list):
        return _legacy_profiles()
    profiles = []
    seen = set()
    for item in configured[:100]:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", ""))
        provider = str(item.get("provider_type", OPENAI_COMPATIBLE)).lower()
        model = str(item.get("model", "")).strip()
        endpoint = str(item.get("base_url", item.get("endpoint", ""))).strip()
        if (
            not _ID_RE.fullmatch(model_id) or model_id in seen
            or provider not in {OPENAI_COMPATIBLE, AZURE_OPENAI}
            or not model or not endpoint.startswith(("https://", "http://"))
        ):
            continue
        profile = {
            "id": model_id,
            "label": str(item.get("label") or item.get("name") or model),
            "provider_type": provider,
            "model": model,
            "base_url": endpoint,
            "api_key_env": _key_env({"provider_type": provider, "api_key_env": item.get("api_key_env")}),
            "legacy": False,
        }
        if provider == AZURE_OPENAI:
            profile["api_version"] = str(item.get("api_version", "v1"))
        reasoning_effort = item.get("reasoning_effort")
        if reasoning_effort:
            profile["reasoning_effort"] = str(reasoning_effort)
        profiles.append(profile)
        seen.add(model_id)
    return profiles


def _fallback_profile_id() -> str:
    return AZURE_MODEL_ID if os.getenv("ASF_LLM_PROVIDER", OPENAI_COMPATIBLE).strip().lower() == AZURE_OPENAI else DEFAULT_MODEL_ID


def active_model_id() -> str:
    requested = os.getenv("ASF_ACTIVE_MODEL", "").strip()
    profiles = model_profiles()
    ids = {profile["id"] for profile in profiles}
    if requested in ids:
        return requested
    fallback = _fallback_profile_id()
    if fallback in ids:
        return fallback
    return profiles[0]["id"] if profiles else ""


def active_profile() -> dict:
    selected = active_model_id()
    profiles = model_profiles()
    return next((profile for profile in profiles if profile["id"] == selected), _legacy_profiles()[0])


def active_provider() -> str:
    return active_profile()["provider_type"]


def _profile_key(profile: dict) -> str:
    key = os.getenv(_key_env(profile), "")
    if key:
        return key
    if profile["provider_type"] == OPENAI_COMPATIBLE and profile.get("legacy"):
        return os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY", "")
    return ""


def profile_configured(profile: dict) -> bool:
    return bool(_profile_key(profile) and profile.get("model") and profile.get("base_url"))


def model_catalog() -> list[dict]:
    """Expose labels and configuration status, never API key values."""
    return [
        {
            "id": profile["id"],
            "label": profile["label"],
            "provider_type": profile["provider_type"],
            "model": profile["model"],
            "base_url": profile["base_url"],
            "api_version": profile.get("api_version", ""),
            "api_key_env": profile["api_key_env"],
            "legacy": bool(profile.get("legacy")),
            "configured": profile_configured(profile),
        }
        for profile in model_profiles()
    ]


def provider_configured() -> bool:
    return profile_configured(active_profile())


def configured_display() -> dict[str, str]:
    """Return non-secret settings for the currently selected model profile."""
    profile = active_profile()
    return {
        "provider_type": profile["provider_type"],
        "base_url": profile["base_url"],
        "model": profile["model"],
        "api_version": profile.get("api_version", ""),
        "model_id": profile["id"],
        "model_label": profile["label"],
    }


def _completion_limit() -> int:
    try:
        return max(1, int(os.getenv("LLM_MAX_TOKENS", "1200")))
    except ValueError:
        return 1200


def create_chat_model(*, stream_usage: bool = True):
    """Build a LangChain model client for the selected profile."""
    profile = active_profile()
    api_key = _profile_key(profile)
    provider = profile["provider_type"]
    if provider == AZURE_OPENAI:
        endpoint = profile["base_url"].strip().rstrip("/")
        deployment = profile["model"]
        api_version = profile.get("api_version", "2024-08-01-preview").strip()
        reasoning_effort = profile.get("reasoning_effort") or os.getenv("AZURE_OPENAI_REASONING_EFFORT", "").strip()
        if api_version.lower() == "v1":
            from langchain_openai import ChatOpenAI

            base_url = endpoint if endpoint.endswith("/openai/v1") else endpoint + "/openai/v1"
            options = {"model": deployment, "base_url": base_url, "api_key": api_key,
                      "max_completion_tokens": _completion_limit(), "stream_usage": stream_usage}
            if reasoning_effort:
                options["reasoning_effort"] = reasoning_effort
            return ChatOpenAI(**options)

        from langchain_openai import AzureChatOpenAI

        options = {"azure_endpoint": endpoint, "azure_deployment": deployment, "api_version": api_version,
                   "api_key": api_key, "max_completion_tokens": _completion_limit(), "stream_usage": stream_usage}
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        return AzureChatOpenAI(**options)

    from langchain_openai import ChatOpenAI

    model_name = profile["model"]
    options = {"model": model_name, "api_key": api_key, "base_url": profile["base_url"], "stream_usage": stream_usage}
    if model_name.lower().startswith(("gpt-5", "o1", "o3", "o4")):
        options["max_completion_tokens"] = _completion_limit()
        if profile.get("reasoning_effort"):
            options["reasoning_effort"] = profile["reasoning_effort"]
    else:
        options["temperature"] = 0
        options["max_tokens"] = _completion_limit()
    return ChatOpenAI(**options)
