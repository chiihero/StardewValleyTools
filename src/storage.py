from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import DEFAULT_OPENAI_BASE_URL, DEFAULT_OPENAI_MODEL, AppSettings, ManagedMod

STATE_DIR_NAME = ".stardewvalleytools"
STATE_FILE_NAME = "state.json"


def project_root() -> Path:
    """返回项目根目录，供状态文件和相对路径计算使用。"""
    return Path(__file__).resolve().parents[1]


def default_state_path() -> Path:
    """返回默认状态文件路径。"""
    return project_root() / STATE_DIR_NAME / STATE_FILE_NAME


def _path_value(value: Path | None) -> str | None:
    """把 Path 值转换成可写入 JSON 的字符串。"""
    return str(value) if value is not None else None


def _load_path(value: Any) -> Path | None:
    """把 JSON 里的路径字符串恢复成 Path 对象。"""
    if not value:
        return None
    return Path(str(value)).expanduser()


def serialize_settings(settings: AppSettings) -> dict[str, Any]:
    """把设置对象转成适合持久化的字典。"""
    return {
        "library_root": _path_value(settings.library_root),
        "game_root": _path_value(settings.game_root),
        "game_mods_root": _path_value(settings.game_mods_root),
        "ai_enabled": settings.ai_enabled,
        "openai_api_key": settings.openai_api_key,
        "openai_model": settings.openai_model,
        "openai_base_url": settings.openai_base_url,
        "translation_enabled": settings.translation_enabled,
        "import_policy": settings.import_policy,
    }


def deserialize_settings(raw: dict[str, Any]) -> AppSettings:
    """从持久化字典恢复设置对象，并补上默认值。"""
    return AppSettings(
        library_root=_load_path(raw.get("library_root")),
        game_root=_load_path(raw.get("game_root")),
        game_mods_root=_load_path(raw.get("game_mods_root")),
        ai_enabled=bool(raw.get("ai_enabled", True)),
        openai_api_key=str(raw.get("openai_api_key") or ""),
        openai_model=str(raw.get("openai_model") or DEFAULT_OPENAI_MODEL),
        openai_base_url=str(raw.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL),
        translation_enabled=bool(raw.get("translation_enabled", True)),
        import_policy=str(raw.get("import_policy") or "overwrite"),
    )


def serialize_mod(record: ManagedMod) -> dict[str, Any]:
    """把单个 Mod 记录转成可序列化字典。"""
    return {
        "source_path": str(record.source_path),
        "enabled": record.enabled,
        "display_name": record.display_name,
        "author": record.author,
        "version": record.version,
        "unique_id": record.unique_id,
        "mod_type": record.mod_type,
        "translation_status": record.translation_status,
        "has_chinese": record.has_chinese,
        "missing_keys_count": record.missing_keys_count,
        "has_manifest": record.has_manifest,
        "manifest_path": _path_value(record.manifest_path),
        "tags": list(record.tags),
        "notes": record.notes,
        "last_scanned": record.last_scanned,
        "warnings": list(record.warnings),
    }


def deserialize_mod(raw: dict[str, Any]) -> ManagedMod:
    """从持久化字典恢复单个 Mod 记录。"""
    return ManagedMod(
        source_path=Path(str(raw.get("source_path") or "")).expanduser(),
        enabled=bool(raw.get("enabled", False)),
        display_name=str(raw.get("display_name") or ""),
        author=raw.get("author") if raw.get("author") is not None else None,
        version=raw.get("version") if raw.get("version") is not None else None,
        unique_id=raw.get("unique_id") if raw.get("unique_id") is not None else None,
        mod_type=str(raw.get("mod_type") or "unknown"),
        translation_status=str(raw.get("translation_status") or "unknown"),
        has_chinese=bool(raw.get("has_chinese", False)),
        missing_keys_count=int(raw.get("missing_keys_count", 0)),
        has_manifest=bool(raw.get("has_manifest", False)),
        manifest_path=_load_path(raw.get("manifest_path")),
        tags=[str(tag) for tag in raw.get("tags", []) if str(tag).strip()],
        notes=str(raw.get("notes") or ""),
        last_scanned=raw.get("last_scanned") if raw.get("last_scanned") is not None else None,
        warnings=[str(item) for item in raw.get("warnings", []) if str(item).strip()],
    )


def load_state(state_path: Path | None = None) -> tuple[AppSettings, dict[str, ManagedMod]]:
    """读取本地状态文件，失败时回退到空设置和空记录。"""
    path = state_path or default_state_path()
    if not path.exists():
        return AppSettings(), {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        settings = deserialize_settings(raw.get("settings") or {})
        mods: dict[str, ManagedMod] = {}
        for item in raw.get("mods", []):
            if not item.get("source_path"):
                continue
            try:
                record = deserialize_mod(item)
            except Exception:
                continue
            key = str(record.source_path.resolve() if record.source_path.exists() else record.source_path)
            mods[key] = record
        return settings, mods
    except Exception:
        return AppSettings(), {}


def save_state(settings: AppSettings, mods: dict[str, ManagedMod], state_path: Path | None = None) -> Path:
    """把当前设置和 Mod 记录原子写回本地状态文件。"""
    path = state_path or default_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": 1,
        "settings": serialize_settings(settings),
        "mods": [serialize_mod(record) for record in sorted(mods.values(), key=lambda item: item.display_name.lower())],
    }

    fd, temp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temp_path = Path(temp_name)
        temp_path.replace(path)
    except Exception:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()
        raise
    return path
