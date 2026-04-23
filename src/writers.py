from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .detector import compare_json_structure


def normalize_json_text(payload: Any) -> str:
    """把任意 JSON 载荷格式化成稳定的漂亮输出文本。"""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def safe_output_path(base_path: Path) -> Path:
    """生成不会覆盖已有文件的输出路径。"""
    candidate = base_path
    index = 1
    while candidate.exists():
        candidate = base_path.with_name(f"{base_path.stem}.{index}{base_path.suffix}")
        index += 1
    return candidate


def validate_translation(source_payload: Any, candidate_payload: Any) -> None:
    """在落盘前检查翻译结果的结构是否和源数据一致。"""
    comparison = compare_json_structure(source_payload, candidate_payload)
    if not comparison.comparable:
        raise ValueError("translated JSON structure does not match the source payload")
    if comparison.status == "partial":
        raise ValueError(
            "translated JSON is missing keys: " + ", ".join(comparison.missing_keys[:10])
        )


def write_json_file(path: Path, payload: Any, source_payload: Any | None = None) -> Path:
    """把 JSON 安全写入磁盘，必要时先校验结构再原子替换。"""
    if source_payload is not None:
        validate_translation(source_payload, payload)

    if path.exists() and path.is_dir():
        raise ValueError(f"output path is a directory: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalize_json_text(payload))
        temp_path = Path(temp_name)
        temp_path.replace(path)
    except Exception:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()
        raise
    return path
