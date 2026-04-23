from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import ManifestKind, ManifestInfo, TranslationStatus

_PLACEHOLDER_PATTERNS = (
    re.compile(r"\{\{[^{}]+\}\}"),
    re.compile(r"\{\d+\}"),
    re.compile(r"%\d*\$?[sdif]"),
    re.compile(r"\[[^\[\]]+\]"),
)


@dataclass(slots=True)
class StructureComparison:
    status: TranslationStatus
    missing_keys_count: int = 0
    missing_keys: list[str] = field(default_factory=list)
    extra_keys: list[str] = field(default_factory=list)
    comparable: bool = True
    notes: list[str] = field(default_factory=list)


def classify_manifest(raw: dict[str, Any], path: Path) -> ManifestInfo:
    """把 manifest.json 的原始字典整理成可直接用于界面的结构化信息。"""
    name = str(raw.get("Name") or path.parent.name)
    author = raw.get("Author")
    unique_id = raw.get("UniqueID")
    description = raw.get("Description")
    version = raw.get("Version")
    entry_dll = raw.get("EntryDll") if isinstance(raw.get("EntryDll"), str) else None
    content_pack_for = None
    content_pack_value = raw.get("ContentPackFor")
    if isinstance(content_pack_value, dict):
        content_pack_for = str(content_pack_value.get("UniqueID") or "") or None
    elif isinstance(content_pack_value, str):
        content_pack_for = content_pack_value
    minimum_api_version = raw.get("MinimumApiVersion") if isinstance(raw.get("MinimumApiVersion"), str) else None
    update_keys_raw = raw.get("UpdateKeys")
    update_keys: list[str] = []
    if isinstance(update_keys_raw, str):
        update_keys = [update_keys_raw]
    elif isinstance(update_keys_raw, list):
        update_keys = [str(item).strip() for item in update_keys_raw if str(item).strip()]

    kind: ManifestKind = "unknown"
    hints: list[str] = []
    if entry_dll:
        kind = "smapi"
        hints.append(f"EntryDll={entry_dll}")
    if content_pack_for:
        kind = "content_pack"
        hints.append(f"ContentPackFor={content_pack_for}")
    if update_keys:
        hints.append(f"UpdateKeys={len(update_keys)}")
    if not hints:
        hints.append("manifest has no EntryDll or ContentPackFor hint")

    return ManifestInfo(
        path=path,
        name=name,
        author=str(author) if author is not None else None,
        unique_id=str(unique_id) if unique_id is not None else None,
        description=str(description) if description is not None else None,
        version=str(version) if version is not None else None,
        entry_dll=entry_dll,
        content_pack_for=content_pack_for,
        minimum_api_version=minimum_api_version,
        update_keys=update_keys,
        kind=kind,
        raw=raw,
    )


def placeholder_tokens(value: str) -> set[str]:
    """提取字符串里所有需要在翻译时保留的占位符标记。"""
    tokens: set[str] = set()
    for pattern in _PLACEHOLDER_PATTERNS:
        tokens.update(pattern.findall(value))
    return tokens


def collect_placeholder_tokens(payload: Any) -> set[str]:
    """递归收集 JSON 载荷中的占位符，供翻译前后校验使用。"""
    tokens: set[str] = set()
    if isinstance(payload, str):
        tokens.update(placeholder_tokens(payload))
    elif isinstance(payload, dict):
        for value in payload.values():
            tokens.update(collect_placeholder_tokens(value))
    elif isinstance(payload, list):
        for value in payload:
            tokens.update(collect_placeholder_tokens(value))
    return tokens


def compare_json_structure(source: Any, candidate: Any, path: str = "") -> StructureComparison:
    """递归比较两份 JSON 的结构，判断翻译结果是否保留原始形状。"""
    if type(source) is not type(candidate):
        return StructureComparison(
            status="unknown",
            comparable=False,
            notes=[f"type mismatch at {path or '<root>'}: {type(source).__name__} vs {type(candidate).__name__}"],
        )

    if isinstance(source, dict):
        missing: list[str] = []
        extra: list[str] = []
        notes: list[str] = []
        comparable = True
        for key, value in source.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key not in candidate:
                missing.append(child_path)
                continue
            child = compare_json_structure(value, candidate[key], child_path)
            missing.extend(child.missing_keys)
            extra.extend(child.extra_keys)
            notes.extend(child.notes)
            comparable = comparable and child.comparable
        for key in candidate.keys():
            if key not in source:
                extra.append(f"{path}.{key}" if path else str(key))
        status: TranslationStatus = "translated"
        if missing or extra:
            status = "partial"
        if not comparable:
            status = "unknown"
        return StructureComparison(
            status=status,
            missing_keys_count=len(missing),
            missing_keys=missing,
            extra_keys=extra,
            comparable=comparable,
            notes=notes,
        )

    if isinstance(source, list):
        missing: list[str] = []
        extra: list[str] = []
        notes: list[str] = []
        comparable = True
        if len(source) != len(candidate):
            notes.append(f"list length mismatch at {path or '<root>'}: {len(source)} vs {len(candidate)}")
            comparable = False
        for index, value in enumerate(source):
            if index >= len(candidate):
                missing.append(f"{path}[{index}]" if path else f"[{index}]")
                continue
            child_path = f"{path}[{index}]" if path else f"[{index}]"
            child = compare_json_structure(value, candidate[index], child_path)
            missing.extend(child.missing_keys)
            extra.extend(child.extra_keys)
            notes.extend(child.notes)
            comparable = comparable and child.comparable
        status: TranslationStatus = "translated"
        if missing or extra:
            status = "partial"
        if not comparable:
            status = "unknown"
        return StructureComparison(
            status=status,
            missing_keys_count=len(missing),
            missing_keys=missing,
            extra_keys=extra,
            comparable=comparable,
            notes=notes,
        )

    tokens = collect_placeholder_tokens(source)
    missing_tokens = [token for token in tokens if token not in str(candidate)]
    if missing_tokens:
        return StructureComparison(
            status="unknown",
            comparable=False,
            notes=[f"placeholder tokens changed at {path or '<root>'}: {', '.join(sorted(missing_tokens))}"],
        )
    return StructureComparison(status="translated")


def compare_locale_payloads(default_payload: Any, zh_payload: Any) -> StructureComparison:
    """比较默认语言和中文语言包的结构完整度。"""
    return compare_json_structure(default_payload, zh_payload)


def count_json_files(paths: Iterable[Path]) -> int:
    """统计一组路径迭代器里实际包含的 JSON 文件数量。"""
    return sum(1 for _ in paths)
