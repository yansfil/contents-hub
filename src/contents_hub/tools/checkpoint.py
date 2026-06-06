"""Agent-callable append-only checkpoint helper."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from contents_hub.tools.base import ToolSpec

logger = logging.getLogger(__name__)


_APPEND_CHECKPOINT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Absolute or repo-local JSONL checkpoint path supplied by the caller.",
        },
        "item": {
            "type": "object",
            "description": "Single raw item JSON object to append.",
            "additionalProperties": True,
        },
        "items": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
            "description": "Optional batch of raw item JSON objects to append.",
        },
    },
    "required": ["path"],
}


async def append_checkpoint_handler(**kwargs: Any) -> str:
    """Append one or more JSON objects to a JSONL checkpoint file.

    The exploration runner creates the path and tells the agent where to write.
    This helper only replaces fragile Bash/Python heredocs with a structured
    tool call.
    """
    raw_path = kwargs.get("path")
    if not raw_path or not isinstance(raw_path, str):
        return json.dumps({"ok": False, "error": "missing or invalid 'path' argument"})

    records: list[dict[str, Any]] = []
    item = kwargs.get("item")
    if isinstance(item, dict):
        records.append(item)
    items = kwargs.get("items")
    if isinstance(items, list):
        records.extend(record for record in items if isinstance(record, dict))

    if not records:
        return json.dumps({"ok": False, "error": "no checkpoint item supplied"})

    path = Path(raw_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("append checkpoint failed: %s", e)
        return json.dumps({"ok": False, "error": f"append checkpoint failed: {e}"})

    return json.dumps({"ok": True, "path": str(path), "items_appended": len(records)})


append_checkpoint = ToolSpec(
    name="append_checkpoint",
    description=(
        "Append one or more candidate raw items to the run-local JSONL "
        "checkpoint file. Use during exploration list/detail harvest instead "
        "of Bash heredocs."
    ),
    input_schema=_APPEND_CHECKPOINT_INPUT_SCHEMA,
    handler=append_checkpoint_handler,
)


def _register_default() -> None:
    try:
        from contents_hub.tools.registry import get_default_registry

        get_default_registry().register(append_checkpoint)
    except Exception:  # noqa: BLE001
        logger.debug("append_checkpoint tool: deferred default-registry registration")


_register_default()


__all__ = ["append_checkpoint", "append_checkpoint_handler"]
