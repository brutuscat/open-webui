"""Explicit model capability detection for GPT-OSS Harmony tools."""

from typing import Any


CAPABILITY = "gpt_oss_harmony_native_tools"


def is_native_harmony_model(model: dict[str, Any] | None) -> bool:
    """Return true only for models explicitly opted into native Harmony tools."""
    metadata = ((model or {}).get("info") or {}).get("meta") or {}
    capabilities = metadata.get("capabilities") or {}
    return capabilities.get(CAPABILITY) is True


def enable_native_namespaces(form_data: dict[str, Any], namespaces: set[str]) -> None:
    """Ask the GPT-OSS template to declare the enabled native namespaces."""
    kwargs = dict(form_data.get("chat_template_kwargs") or {})
    kwargs["builtin_tools"] = sorted(namespaces)
    form_data["chat_template_kwargs"] = kwargs
