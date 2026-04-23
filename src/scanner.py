from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .detector import classify_manifest, compare_locale_payloads
from .models import ModAnalysis


def _load_json(path: Path) -> Any:
    """用 UTF-8 BOM 兼容方式读取 JSON 文件。"""
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _discover_json_files(folder: Path) -> list[Path]:
    """列出目录下所有 JSON 文件，并按文件名排序。"""
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".json")


def _is_tree_locale(folder: Path) -> bool:
    """判断一个路径是否是 tree 风格的语言目录。"""
    return folder.exists() and folder.is_dir()


def _scan_locale_layout(i18n_dir: Path, warnings: list[str]) -> tuple[
    Path | None,
    Path | None,
    Path | None,
    Path | None,
    str,
    str,
    bool,
    int,
    list[Path],
    bool,
]:
    """扫描 i18n 目录，识别 flat/tree 两种语言布局并统计完整度。"""
    default_flat = i18n_dir / "default.json"
    zh_flat = i18n_dir / "zh.json"
    default_tree = i18n_dir / "default"
    zh_tree = i18n_dir / "zh"

    default_layout = "none"
    zh_layout = "none"
    default_root: Path | None = None
    zh_root: Path | None = None
    default_primary: Path | None = None
    zh_primary: Path | None = None
    source_paths: list[Path] = []
    missing_keys_count = 0
    has_chinese = False
    layout_mismatch = False

    if default_flat.exists() and default_flat.is_file():
        default_layout = "flat"
        default_root = i18n_dir
        default_primary = default_flat
        source_paths = [default_flat]
    elif _is_tree_locale(default_tree):
        source_paths = _discover_json_files(default_tree)
        if source_paths:
            default_layout = "tree"
            default_root = default_tree
            default_primary = default_tree
        else:
            warnings.append("default locale directory exists but contains no JSON files")

    if zh_flat.exists() and zh_flat.is_file():
        zh_layout = "flat"
        zh_root = i18n_dir
        zh_primary = zh_flat
        has_chinese = True
    elif _is_tree_locale(zh_tree):
        zh_files = _discover_json_files(zh_tree)
        if zh_files:
            zh_layout = "tree"
            zh_root = zh_tree
            zh_primary = zh_tree
            has_chinese = True
        else:
            warnings.append("zh locale directory exists but contains no JSON files")

    if default_layout != "none" and zh_layout != "none" and default_layout != zh_layout:
        layout_mismatch = True
        warnings.append(f"locale layout mismatch: default is {default_layout}, zh is {zh_layout}")

    if i18n_dir.exists() and i18n_dir.is_dir():
        unsupported = [p for p in i18n_dir.iterdir() if p.is_file() and p.suffix.lower() != ".json"]
        if unsupported and not source_paths and not has_chinese:
            warnings.append(
                "i18n folder exists, but only non-JSON localization files were found: "
                + ", ".join(p.name for p in unsupported)
            )

    if default_layout == "flat" and zh_layout == "flat":
        try:
            default_payload = _load_json(default_flat)
            zh_payload = _load_json(zh_flat)
            comparison = compare_locale_payloads(default_payload, zh_payload)
            missing_keys_count = comparison.missing_keys_count
            if comparison.status == "partial":
                warnings.extend(comparison.notes)
        except Exception as exc:
            warnings.append(f"failed to compare default.json and zh.json: {exc}")
            return (
                default_primary,
                zh_primary,
                default_root,
                zh_root,
                default_layout,
                zh_layout,
                has_chinese,
                missing_keys_count,
                source_paths,
                layout_mismatch,
            )

    if default_layout == "tree" and zh_layout == "tree":
        default_files = {p.relative_to(default_tree): p for p in source_paths}
        zh_files = {p.relative_to(zh_tree): p for p in _discover_json_files(zh_tree)}
        missing_files = sorted(default_files.keys() - zh_files.keys())
        extra_files = sorted(zh_files.keys() - default_files.keys())
        if missing_files:
            missing_keys_count += len(missing_files)
            warnings.append(
                "missing zh locale files: " + ", ".join(path.as_posix() for path in missing_files)
            )
        if extra_files:
            warnings.append("extra zh locale files: " + ", ".join(path.as_posix() for path in extra_files))
        for rel_path, default_path in default_files.items():
            zh_path = zh_files.get(rel_path)
            if zh_path is None:
                continue
            try:
                default_payload = _load_json(default_path)
                zh_payload = _load_json(zh_path)
                comparison = compare_locale_payloads(default_payload, zh_payload)
                missing_keys_count += comparison.missing_keys_count
                if comparison.status == "partial":
                    warnings.extend([f"{rel_path.as_posix()}: {note}" for note in comparison.notes])
            except Exception as exc:
                warnings.append(f"failed to compare {rel_path.as_posix()}: {exc}")

    return (
        default_primary,
        zh_primary,
        default_root,
        zh_root,
        default_layout,
        zh_layout,
        has_chinese,
        missing_keys_count,
        source_paths,
        layout_mismatch,
    )


def scan_mod(mod_path: Path) -> ModAnalysis:
    """扫描单个 Mod 目录，生成用于界面展示和后续处理的分析结果。"""
    mod_path = mod_path.expanduser().resolve()
    warnings: list[str] = []
    manifest_path = mod_path / "manifest.json"
    has_manifest = manifest_path.is_file()
    manifest = None
    mod_name = mod_path.name
    mod_type = "unknown"
    manifest_hints: list[str] = []

    if has_manifest:
        try:
            raw_manifest = _load_json(manifest_path)
            if not isinstance(raw_manifest, dict):
                warnings.append("manifest.json did not contain a JSON object")
            else:
                manifest = classify_manifest(raw_manifest, manifest_path)
                mod_name = manifest.name
                mod_type = manifest.kind
                manifest_hints = []
                if manifest.entry_dll:
                    manifest_hints.append(f"EntryDll={manifest.entry_dll}")
                if manifest.content_pack_for:
                    manifest_hints.append(f"ContentPackFor={manifest.content_pack_for}")
                if not manifest_hints:
                    warnings.append("manifest.json has no EntryDll or ContentPackFor hint")
        except Exception as exc:
            warnings.append(f"failed to parse manifest.json: {exc}")
    else:
        warnings.append("manifest.json was not found at the selected folder root")

    i18n_dir = mod_path / "i18n"
    default_primary, zh_primary, default_root, zh_root, default_layout, zh_layout, has_chinese, missing_keys_count, source_paths, layout_mismatch = _scan_locale_layout(i18n_dir, warnings)

    translation_status = "unknown"
    if default_layout != "none" and zh_layout == "none":
        translation_status = "not_translated"
    elif default_layout != "none" and zh_layout != "none" and not layout_mismatch:
        translation_status = "partial" if missing_keys_count else "translated"
    elif zh_layout != "none" and default_layout == "none":
        translation_status = "translated"

    return ModAnalysis(
        mod_path=mod_path,
        mod_name=mod_name,
        has_manifest=has_manifest,
        manifest_path=manifest_path if has_manifest else None,
        manifest=manifest,
        mod_type=mod_type,
        manifest_hints=manifest_hints,
        translation_status=translation_status,
        has_chinese=has_chinese,
        default_locale_path=default_primary,
        zh_locale_path=zh_primary,
        default_locale_root=default_root,
        zh_locale_root=zh_root,
        default_layout=default_layout,
        zh_layout=zh_layout,
        missing_keys_count=missing_keys_count,
        translatable_sources=source_paths,
        warnings=warnings,
    )
