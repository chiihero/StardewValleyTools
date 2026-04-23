from __future__ import annotations

from datetime import datetime
from pathlib import Path
from shutil import copytree
from typing import Callable, Iterable

from .models import AppSettings, ImportReport, ManagedMod, ModAnalysis
from .scanner import scan_mod


def discover_mod_roots(library_root: Path) -> list[Path]:
    """在 Mod 库目录下查找所有包含 manifest.json 的 Mod 根目录。"""
    if not library_root.exists() or not library_root.is_dir():
        return []

    roots: list[Path] = []
    seen: set[str] = set()
    for manifest_path in library_root.rglob("manifest.json"):
        if not manifest_path.is_file():
            continue
        mod_root = manifest_path.parent.resolve()
        key = str(mod_root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(mod_root)

    roots.sort(key=lambda path: path.as_posix().lower())
    return roots


def _build_record(analysis: ModAnalysis, existing: ManagedMod | None) -> ManagedMod:
    """把扫描结果和已有管理器记录合并成最终展示用的记录对象。"""
    manifest = analysis.manifest
    return ManagedMod(
        source_path=analysis.mod_path,
        enabled=existing.enabled if existing is not None else False,
        display_name=analysis.mod_name,
        author=manifest.author if manifest is not None else None,
        version=manifest.version if manifest is not None else None,
        unique_id=manifest.unique_id if manifest is not None else None,
        mod_type=analysis.mod_type,
        translation_status=analysis.translation_status,
        has_chinese=analysis.has_chinese,
        missing_keys_count=analysis.missing_keys_count,
        has_manifest=analysis.has_manifest,
        manifest_path=analysis.manifest_path,
        tags=list(existing.tags) if existing is not None else [],
        notes=existing.notes if existing is not None else "",
        last_scanned=datetime.now().isoformat(timespec="seconds"),
        warnings=list(analysis.warnings),
        analysis=analysis,
    )


def scan_library(library_root: Path, existing_records: dict[str, ManagedMod] | None = None) -> list[ManagedMod]:
    """扫描整个 Mod 库，并尽量保留原有启用状态和备注信息。"""
    existing_records = existing_records or {}
    records: list[ManagedMod] = []

    for mod_root in discover_mod_roots(library_root):
        key = str(mod_root.resolve())
        existing = existing_records.get(key) or existing_records.get(str(mod_root))
        analysis = scan_mod(mod_root)
        records.append(_build_record(analysis, existing))

    records.sort(key=lambda item: item.display_name.lower())
    return records


def resolve_game_mods_root(settings: AppSettings) -> Path | None:
    """根据设置解析游戏 Mods 目录，优先使用显式指定路径。"""
    if settings.game_mods_root is not None:
        return settings.game_mods_root.expanduser()
    if settings.game_root is not None:
        return settings.game_root.expanduser() / "Mods"
    return None


def deploy_enabled_mods(
    mods: Iterable[ManagedMod],
    game_mods_root: Path,
    policy: str = "overwrite",
    progress_callback: Callable[[int, int, ManagedMod, str], None] | None = None,
) -> ImportReport:
    """把已启用的 Mod 复制到游戏目录，并按策略处理冲突。"""
    report = ImportReport(game_mods_root=game_mods_root, policy=policy)
    game_mods_root.mkdir(parents=True, exist_ok=True)

    enabled_mods = [record for record in mods if record.enabled]
    total = len(enabled_mods)

    for index, record in enumerate(enabled_mods, start=1):
        if progress_callback is not None:
            progress_callback(index, total, record, "processing")

        source_path = record.source_path
        destination = game_mods_root / source_path.name

        try:
            if source_path.resolve() == destination.resolve():
                report.skipped.append(source_path)
                if progress_callback is not None:
                    progress_callback(index, total, record, "skipped")
                continue

            if destination.exists() and policy == "skip":
                report.skipped.append(source_path)
                if progress_callback is not None:
                    progress_callback(index, total, record, "skipped")
                continue

            copytree(source_path, destination, dirs_exist_ok=destination.exists())
            report.copied.append(source_path)
            if progress_callback is not None:
                progress_callback(index, total, record, "copied")
        except Exception as exc:
            report.failed.append((source_path, str(exc)))
            if progress_callback is not None:
                progress_callback(index, total, record, "failed")

    return report
