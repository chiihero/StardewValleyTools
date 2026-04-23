from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

TranslationStatus = Literal["translated", "partial", "not_translated", "unknown"]
ManifestKind = Literal["smapi", "content_pack", "unknown"]
LocaleLayout = Literal["flat", "tree", "none"]


@dataclass(slots=True)
class ManifestInfo:
    path: Path
    name: str
    author: str | None = None
    unique_id: str | None = None
    description: str | None = None
    version: str | None = None
    entry_dll: str | None = None
    content_pack_for: str | None = None
    minimum_api_version: str | None = None
    kind: ManifestKind = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModAnalysis:
    mod_path: Path
    mod_name: str
    has_manifest: bool
    manifest_path: Path | None = None
    manifest: ManifestInfo | None = None
    mod_type: ManifestKind = "unknown"
    manifest_hints: list[str] = field(default_factory=list)
    translation_status: TranslationStatus = "unknown"
    has_chinese: bool = False
    default_locale_path: Path | None = None
    zh_locale_path: Path | None = None
    default_locale_root: Path | None = None
    zh_locale_root: Path | None = None
    default_layout: LocaleLayout = "none"
    zh_layout: LocaleLayout = "none"
    missing_keys_count: int = 0
    translatable_sources: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TranslationPlan:
    mod_path: Path
    source_paths: list[Path]
    output_path: Path
    payload: Any


@dataclass(slots=True)
class TranslationResult:
    output_path: Path
    source_paths: list[Path]
    payload: Any


@dataclass(slots=True)
class AppSettings:
    library_root: Path | None = None
    game_root: Path | None = None
    game_mods_root: Path | None = None
    ai_enabled: bool = True
    ai_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    translation_enabled: bool = True
    import_policy: str = "overwrite"


@dataclass(slots=True)
class ManagedMod:
    source_path: Path
    enabled: bool = False
    display_name: str = ""
    author: str | None = None
    version: str | None = None
    unique_id: str | None = None
    mod_type: ManifestKind = "unknown"
    translation_status: TranslationStatus = "unknown"
    has_chinese: bool = False
    missing_keys_count: int = 0
    has_manifest: bool = False
    manifest_path: Path | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    last_scanned: str | None = None
    warnings: list[str] = field(default_factory=list)
    analysis: ModAnalysis | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class ImportReport:
    game_mods_root: Path
    copied: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    policy: str = "overwrite"


@dataclass(slots=True)
class WorkerEvent:
    kind: str
    message: str = ""
    analysis: ModAnalysis | None = None
    result: TranslationResult | None = None
    mods: list[ManagedMod] = field(default_factory=list)
    import_report: ImportReport | None = None
