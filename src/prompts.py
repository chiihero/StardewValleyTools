from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .detector import collect_placeholder_tokens

SYSTEM_PROMPT = (
    "You translate Stardew Valley mod JSON locale data into Simplified Chinese. "
    "Return JSON only. Preserve object keys, list lengths, numbers, booleans, nulls, and every placeholder token exactly as-is. "
    "Translate only human-readable string values."
)


def build_translation_prompt(payload: Any, source_paths: list[Path], output_name: str) -> str:
    tokens = sorted(collect_placeholder_tokens(payload))
    sources = "\n".join(f"- {path.as_posix()}" for path in source_paths)
    token_block = ", ".join(tokens) if tokens else "(none detected)"
    return (
        "Translate the following JSON payload into Simplified Chinese.\n\n"
        f"Source files:\n{sources}\n\n"
        f"Output file name: {output_name}\n\n"
        "Rules:\n"
        "- Return valid JSON only. No markdown, no code fences, no commentary.\n"
        "- Keep the same structure and the same keys.\n"
        "- Do not translate keys.\n"
        "- Preserve these placeholder-like tokens exactly when they appear: "
        f"{token_block}.\n"
        "- Do not remove or add fields.\n\n"
        "JSON payload:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
