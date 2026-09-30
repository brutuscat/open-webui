"""Privacy-safe structured metering for Harmony-native tools."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def emit_native_tool_event(event: dict[str, Any]) -> None:
    """Emit metadata only; prompts, page text, and command output are excluded."""
    logger.info("gpt_oss_native_tool_event=%s", json.dumps(event, sort_keys=True))
