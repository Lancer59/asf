"""Provider selection shared by chat and skill-revision model calls."""
from __future__ import annotations

import os


OPENAI_COMPATIBLE = "openai_compatible"
AZURE_OPENAI = "azure_openai"


def active_provider() -> str:
    provider = os.getenv("ASF_LLM_PROVIDER", OPENAI_COMPATIBLE).strip().lower()
    return provider if provider in {OPENAI_COMPATIBLE, AZURE_OPENAI} else OPENAI_COMPATIBLE


def provider_configured() -> bool:
    if active_provider() == AZURE_OPENAI:
        return bool(
            os.getenv("AZURE_OPENAI_API_KEY")
            and os.getenv("AZURE_OPENAI_ENDPOINT")
            and os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        )
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY"))


def configured_display() -> dict[str, str]:
    """Return non-secret settings for the currently selected provider."""
    if active_provider() == AZURE_OPENAI:
        return {
            "provider_type": AZURE_OPENAI,
            "base_url": os.getenv("AZURE_OPENAI_ENDPOINT", "https://servicedeskgpt.openai.azure.com/"),
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.3-chat"),
            "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        }
    return {
        "provider_type": OPENAI_COMPATIBLE,
        "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.openai.com/v1"),
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "api_version": "",
    }


def create_chat_model(*, stream_usage: bool = True):
    """Build a LangChain model client for the active provider profile."""
    if active_provider() == AZURE_OPENAI:
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "").strip()
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()
        api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        # Azure's v1 endpoint uses the standard OpenAI client and does not need
        # a dated api-version. Keep dated deployments available for resources
        # that have not moved to v1 yet.
        if api_version.lower() == "v1":
            from langchain_openai import ChatOpenAI

            base_url = endpoint if endpoint.endswith("/openai/v1") else endpoint + "/openai/v1"
            options = {
                "model": deployment,
                "base_url": base_url,
                "api_key": api_key,
                "max_completion_tokens": int(os.getenv("LLM_MAX_TOKENS", "1200")),
                "stream_usage": stream_usage,
            }
            reasoning_effort = os.getenv("AZURE_OPENAI_REASONING_EFFORT", "").strip()
            if reasoning_effort:
                options["reasoning_effort"] = reasoning_effort
            return ChatOpenAI(**options)

        from langchain_openai import AzureChatOpenAI

        options = {
            "azure_endpoint": endpoint,
            "azure_deployment": deployment,
            "api_version": api_version,
            "api_key": api_key,
            # GPT-5 models use max_completion_tokens and do not accept temperature.
            "max_completion_tokens": int(os.getenv("LLM_MAX_TOKENS", "1200")),
            "stream_usage": stream_usage,
        }
        reasoning_effort = os.getenv("AZURE_OPENAI_REASONING_EFFORT", "").strip()
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        return AzureChatOpenAI(**options)

    from langchain_openai import ChatOpenAI

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("GROQ_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_COMPAT_BASE_URL") or (
        "https://api.groq.com/openai/v1" if os.getenv("GROQ_API_KEY") and not os.getenv("OPENAI_API_KEY") else None
    )
    options = {
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "api_key": api_key,
        "temperature": 0,
        "stream_usage": stream_usage,
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1200")),
    }
    if base_url:
        options["base_url"] = base_url
    return ChatOpenAI(**options)
