from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .detector import collect_placeholder_tokens, compare_json_structure
from .models import DEFAULT_OPENAI_MODEL, ModAnalysis, TranslationPlan, TranslationResult
from .prompts import SYSTEM_PROMPT, build_translation_prompt


def _load_json(path: Path) -> Any:
    """读取待翻译源文件，兼容带 BOM 的 JSON。"""
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _build_source_payload(analysis: ModAnalysis) -> tuple[Any, list[Path]]:
    """把分析结果里的可翻译文件整理成 OpenAI 输入载荷。"""
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
    """从 OpenAI 返回对象里尽量提取纯文本内容。"""
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
    """去掉模型偶尔加上的代码块围栏。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _extract_json(text: str) -> Any:
    """从模型输出中提取并解析 JSON。"""
    cleaned = _strip_code_fences(text)
    start = min((index for index in [cleaned.find("{"), cleaned.find("[")] if index != -1), default=-1)
    if start > 0:
        cleaned = cleaned[start:]
    return json.loads(cleaned)


def plan_translation(analysis: ModAnalysis) -> TranslationPlan:
    """根据分析结果生成一次翻译任务的输入、输出和载荷。"""
    payload, source_paths = _build_source_payload(analysis)
    i18n_dir = analysis.mod_path / "i18n"
    output_path = i18n_dir / "zh.json"
    return TranslationPlan(mod_path=analysis.mod_path, source_paths=source_paths, output_path=output_path, payload=payload)


def translate_with_openai(
    analysis: ModAnalysis,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> TranslationResult:
    """调用 OpenAI 执行正式汉化，并校验返回结构和占位符。"""
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
    chosen_model = model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
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


def probe_openai_connection(api_key: str | None = None, model: str | None = None, base_url: str | None = None) -> str:
    """使用当前 OpenAI 配置发起一次最小请求，用于测试连通性。"""
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not installed") from exc

    client = OpenAI(api_key=key, base_url=base_url or None)
    chosen_model = model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    response = client.responses.create(model=chosen_model, input="请只回复 OK。")
    text = _extract_response_text(response).strip()
    if not text:
        raise ValueError("OpenAI test response was empty")
    return text
