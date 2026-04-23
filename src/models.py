from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

TranslationStatus = Literal["translated", "partial", "not_translated", "unknown"]
ManifestKind = Literal["smapi", "content_pack", "unknown"]
LocaleLayout = Literal["flat", "tree", "none"]
UpdateStatus = Literal["unknown", "no_source", "up_to_date", "outdated", "failed", "installed"]
DEFAULT_OPENAI_MODEL = "gpt-5.4-nano"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass(slots=True)
class NexusUpdateInfo:
    """保存单个 Mod 的 Nexus 更新检查结果。"""
    status: UpdateStatus = "unknown"
    mod_id: int | None = None
    file_id: int | None = None
    current_version: str | None = None
    latest_version: str | None = None
    file_name: str | None = None
    download_url: str | None = None
    update_url: str | None = None
    checked_at: str | None = None
    message: str = ""


@dataclass(slots=True)
class NexusDownloadResult:
    """保存一次 Nexus 下载与安装过程的结果。"""
    status: str = "unknown"
    downloaded_path: Path | None = None
    extracted_path: Path | None = None
    installed_path: Path | None = None
    message: str = ""


@dataclass(slots=True)
class ManifestInfo:
    """保存 manifest.json 解析后的基础信息。"""
    path: Path
    name: str
    author: str | None = None
    unique_id: str | None = None
    description: str | None = None
    version: str | None = None
    entry_dll: str | None = None
    content_pack_for: str | None = None
    minimum_api_version: str | None = None
    update_keys: list[str] = field(default_factory=list)
    kind: ManifestKind = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModAnalysis:
    """保存单个 Mod 扫描后的分析结果。"""
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
    """描述一次 AI 汉化任务需要的输入和输出。"""
    mod_path: Path
    source_paths: list[Path]
    output_path: Path
    payload: Any


@dataclass(slots=True)
class TranslationResult:
    """保存 AI 汉化完成后的结果数据。"""
    output_path: Path
    source_paths: list[Path]
    payload: Any


@dataclass(slots=True)
class AppSettings:
    """保存程序设置与 AI/导入相关配置。"""
    library_root: Path | None = None
    game_root: Path | None = None
    game_mods_root: Path | None = None
    ai_enabled: bool = True
    nexus_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    translation_enabled: bool = True
    import_policy: str = "overwrite"


@dataclass(slots=True)
class ManagedMod:
    """保存 Mod 管理器里的展示记录和用户备注。"""
    source_path: Path
    checked: bool = False
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
    nexus_mod_id: int | None = None
    nexus_file_id: int | None = None
    nexus_update_status: UpdateStatus = "unknown"
    nexus_current_version: str | None = None
    nexus_latest_version: str | None = None
    nexus_file_name: str | None = None
    nexus_update_url: str | None = None
    nexus_download_url: str | None = None
    nexus_last_checked: str | None = None
    nexus_message: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    last_scanned: str | None = None
    warnings: list[str] = field(default_factory=list)
    analysis: ModAnalysis | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class ImportReport:
    """保存导入流程的结果汇总。"""
    game_mods_root: Path
    copied: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    policy: str = "overwrite"


@dataclass(slots=True)
class WorkerEvent:
    """后台线程通过队列发回给 UI 的事件消息。"""
    kind: str
    message: str = ""
    analysis: ModAnalysis | None = None
    result: TranslationResult | None = None
    mods: list[ManagedMod] = field(default_factory=list)
    import_report: ImportReport | None = None
    progress: int | None = None
    total: int | None = None
    summary: str = ""
