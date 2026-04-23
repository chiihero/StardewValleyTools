from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .detector import collect_placeholder_tokens, compare_json_structure
from .models import ModAnalysis, TranslationPlan, TranslationResult
from .prompts import SYSTEM_PROMPT, build_translation_prompt
from .writers import safe_output_path


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _build_source_payload(analysis: ModAnalysis) -> tuple[Any, list[Path]]:
    if not analysis.translatable_sources:
        raise ValueError("no default locale JSON files were found")

    source_paths = list(analysis.translatable_sources)
    if len(source_paths) == 1 and analysis.default_layout == "flat":
        return _load_json(source_paths[0]), source_paths

    payload: dict[str, Any] = {}
    if analysis.default_layout == "tree" and analysis.default_locale_root is not None:
        for path in source_paths:
            payload[path.relative_to(analysis.default_locale_root).as_posix()] = _load_json(path)
    else:
        for path in source_paths:
            payload[path.name] = _load_json(path)
    return payload, source_paths


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text

    outputs = getattr(response, "output", None) or []
    chunks: list[str] = []
    for item in outputs:
        content = getattr(item, "content", None) or []
        for piece in content:
            piece_text = getattr(piece, "text", None)
            if isinstance(piece_text, str):
                chunks.append(piece_text)
    combined = "".join(chunks).strip()
    if not combined:
        raise ValueError("OpenAI response did not contain text content")
    return combined


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _extract_json(text: str) -> Any:
    cleaned = _strip_code_fences(text)
    start = min((index for index in [cleaned.find("{"), cleaned.find("[")] if index != -1), default=-1)
    if start > 0:
        cleaned = cleaned[start:]
    return json.loads(cleaned)


def plan_translation(analysis: ModAnalysis) -> TranslationPlan:
    payload, source_paths = _build_source_payload(analysis)
    i18n_dir = analysis.mod_path / "i18n"
    output_path = safe_output_path(i18n_dir / "zh.generated.json")
    return TranslationPlan(mod_path=analysis.mod_path, source_paths=source_paths, output_path=output_path, payload=payload)


def translate_with_openai(
    analysis: ModAnalysis,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> TranslationResult:
    plan = plan_translation(analysis)

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not installed") from exc

    client = OpenAI(api_key=key, base_url=base_url or None)
    prompt = build_translation_prompt(plan.payload, plan.source_paths, plan.output_path.name)
    chosen_model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    response = client.responses.create(model=chosen_model, instructions=SYSTEM_PROMPT, input=prompt)
    raw_text = _extract_response_text(response)
    candidate = _extract_json(raw_text)

    comparison = compare_json_structure(plan.payload, candidate)
    if not comparison.comparable:
        raise ValueError("OpenAI returned JSON with a different structure")
    if comparison.status == "partial":
        raise ValueError("OpenAI returned JSON with missing keys")

    tokens = collect_placeholder_tokens(plan.payload)
    for token in tokens:
        if token not in json.dumps(candidate, ensure_ascii=False):
            raise ValueError(f"OpenAI response dropped placeholder token: {token}")

    return TranslationResult(output_path=plan.output_path, source_paths=plan.source_paths, payload=candidate)
