from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, BooleanVar, StringVar, Tk, filedialog, messagebox, ttk, Text

from .manager import deploy_enabled_mods, resolve_game_mods_root, scan_library
from .models import AppSettings, ManagedMod, ModAnalysis, WorkerEvent
from .scanner import scan_mod
from .storage import load_state, save_state
from .translator import translate_with_openai
from .writers import write_json_file

IMPORT_POLICY_LABELS = {
    "overwrite": "覆盖",
    "skip": "跳过",
    "prompt": "询问",
}
IMPORT_POLICY_VALUES = {label: value for value, label in IMPORT_POLICY_LABELS.items()}

OPENAI_LABEL = "OpenAI"


class ModManagerApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("Stardew Valley Mod Manager")
        self.root.geometry("1220x820")
        self.root.minsize(980, 680)

        self._queue: queue.Queue[WorkerEvent] = queue.Queue()
        self._worker_running = False

        self._settings, loaded_mods = load_state()
        self._mods_by_path: dict[str, ManagedMod] = dict(loaded_mods)
        self._current_analysis: ModAnalysis | None = None
        self._current_selected_path: str | None = None

        self._folder_var = StringVar(value="")
        self._search_var = StringVar(value="")
        self._library_root_var = StringVar(value="")
        self._game_root_var = StringVar(value="")
        self._game_mods_root_var = StringVar(value="")
        self._ai_folder_var = StringVar(value="")
        self._ai_provider_var = StringVar(value=OPENAI_LABEL)
        self._openai_key_var = StringVar(value="")
        self._openai_model_var = StringVar(value="gpt-4o-mini")
        self._openai_base_url_var = StringVar(value="")
        self._import_policy_var = StringVar(value=IMPORT_POLICY_LABELS["overwrite"])
        self._ai_enabled_var = BooleanVar(value=True)
        self._translation_enabled_var = BooleanVar(value=True)
        self._tags_var = StringVar(value="")
        self._library_summary_var = StringVar(value="")

        self._sort_column = "display_name"
        self._sort_reverse = False

        self._setup_style()
        self._build_ui()
        self._apply_settings_to_form(self._settings)
        self._search_var.trace_add("write", lambda *_: self._refresh_mod_tree())
        self._refresh_mod_tree()
        self._sync_button_states()
        self._poll_queue()

    def run(self) -> None:
        self.root.mainloop()

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(background="#f4f1ea")
        style.configure("TFrame", background="#f4f1ea")
        style.configure("TLabelframe", background="#f4f1ea")
        style.configure("TLabelframe.Label", background="#f4f1ea", foreground="#3a332b")
        style.configure("TLabel", background="#f4f1ea", foreground="#3a332b")
        style.configure("TButton", padding=(10, 6))
        style.configure("Accent.TButton", padding=(10, 6))
        style.configure("TEntry", padding=(6, 4))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=BOTH, expand=True)

        self._notebook = ttk.Notebook(outer)
        self._notebook.pack(fill=BOTH, expand=True)

        self._build_library_tab()
        self._build_ai_tab()
        self._build_settings_tab()
        self._build_log_panel(outer)

        footer = ttk.Frame(outer)
        footer.pack(fill=X, pady=(8, 0))
        self._status_var = StringVar(value="Idle")
        ttk.Label(footer, textvariable=self._status_var).pack(side=LEFT)

    def _build_library_tab(self) -> None:
        tab = ttk.Frame(self._notebook, padding=12)
        self._notebook.add(tab, text="Mod 管理")

        path_box = ttk.LabelFrame(tab, text="Mod 库设置")
        path_box.pack(fill=X)

        row = ttk.Frame(path_box)
        row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Label(row, text="库目录").pack(side=LEFT)
        self._library_root_entry = ttk.Entry(row, textvariable=self._library_root_var)
        self._library_root_entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 8))
        ttk.Button(row, text="浏览", command=lambda: self._choose_directory(self._library_root_var, "选择 Mod 库目录")).pack(side=LEFT)
        self._rescan_button = ttk.Button(row, text="重新扫描", command=self._scan_library_action)
        self._rescan_button.pack(side=LEFT, padx=(8, 0))
        self._import_button = ttk.Button(row, text="导入启用项", command=self._import_enabled_mods)
        self._import_button.pack(side=LEFT, padx=(8, 0))

        search_row = ttk.Frame(path_box)
        search_row.pack(fill=X, padx=8, pady=(0, 8))
        ttk.Label(search_row, text="搜索").pack(side=LEFT)
        search_entry = ttk.Entry(search_row, textvariable=self._search_var)
        search_entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

        ttk.Label(path_box, textvariable=self._library_summary_var, foreground="#655748").pack(anchor="w", padx=8, pady=(0, 8))

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill=BOTH, expand=True, pady=(12, 0))

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        list_box = ttk.LabelFrame(left, text="Mod 列表")
        list_box.pack(fill=BOTH, expand=True)

        columns = ("enabled", "display_name", "mod_type", "version", "author", "translation_status", "path")
        self._mods_tree = ttk.Treeview(list_box, columns=columns, show="headings", selectmode="browse")
        headings = {
            "enabled": "启用",
            "display_name": "名称",
            "mod_type": "类型",
            "version": "版本",
            "author": "作者",
            "translation_status": "汉化状态",
            "path": "路径",
        }
        widths = {
            "enabled": 70,
            "display_name": 180,
            "mod_type": 100,
            "version": 90,
            "author": 140,
            "translation_status": 100,
            "path": 320,
        }
        for column in columns:
            self._mods_tree.heading(column, text=headings[column], command=lambda c=column: self._toggle_sort(c))
            self._mods_tree.column(column, width=widths[column], anchor="w", stretch=True)

        tree_frame = ttk.Frame(list_box)
        tree_frame.pack(fill=BOTH, expand=True, padx=8, pady=(8, 0))
        tree_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self._mods_tree.yview)
        tree_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._mods_tree.xview)
        self._mods_tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        tree_y.pack(side=RIGHT, fill=Y)
        self._mods_tree.pack(side=LEFT, fill=BOTH, expand=True)
        tree_x.pack(side="bottom", fill=X)
        self._mods_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._mods_tree.bind("<Double-1>", lambda _event: self._toggle_selected_mod())

        action_row = ttk.Frame(left)
        action_row.pack(fill=X, pady=(8, 0))
        self._enable_button = ttk.Button(action_row, text="启用", command=lambda: self._set_selected_enabled(True))
        self._enable_button.pack(side=LEFT)
        self._disable_button = ttk.Button(action_row, text="停用", command=lambda: self._set_selected_enabled(False))
        self._disable_button.pack(side=LEFT, padx=(8, 0))
        self._toggle_button = ttk.Button(action_row, text="切换状态", command=self._toggle_selected_mod)
        self._toggle_button.pack(side=LEFT, padx=(8, 0))
        self._apply_metadata_button = ttk.Button(action_row, text="保存备注", command=self._apply_selected_metadata)
        self._apply_metadata_button.pack(side=LEFT, padx=(8, 0))

        detail_box = ttk.LabelFrame(right, text="详情")
        detail_box.pack(fill=BOTH, expand=True)
        self._detail_text = Text(detail_box, height=18, wrap="word", borderwidth=0, background="#fffdf8", foreground="#2f2922")
        self._detail_text.pack(fill=BOTH, expand=True, padx=8, pady=8)
        self._detail_text.configure(state="disabled")

        meta_box = ttk.LabelFrame(right, text="标签 / 备注")
        meta_box.pack(fill=BOTH, expand=False, pady=(10, 0))

        tags_row = ttk.Frame(meta_box)
        tags_row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Label(tags_row, text="标签").pack(side=LEFT)
        self._tags_entry = ttk.Entry(tags_row, textvariable=self._tags_var)
        self._tags_entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

        ttk.Label(meta_box, text="备注").pack(anchor="w", padx=8, pady=(4, 0))
        self._notes_text = Text(meta_box, height=6, wrap="word", borderwidth=0, background="#fffdf8", foreground="#2f2922")
        self._notes_text.pack(fill=BOTH, expand=True, padx=8, pady=(4, 8))

        ttk.Label(tab, text="提示：启用 / 停用只修改记录，不会直接改游戏目录。导入时才复制到游戏 Mods。", foreground="#655748").pack(anchor="w", pady=(8, 0))

    def _build_ai_tab(self) -> None:
        tab = ttk.Frame(self._notebook, padding=12)
        self._notebook.add(tab, text="AI 汉化")

        top = ttk.LabelFrame(tab, text="汉化目标")
        top.pack(fill=X)

        row = ttk.Frame(top)
        row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Label(row, text="文件夹").pack(side=LEFT)
        self._ai_folder_entry = ttk.Entry(row, textvariable=self._ai_folder_var)
        self._ai_folder_entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 8))
        ttk.Button(row, text="浏览", command=lambda: self._choose_directory(self._ai_folder_var, "选择要汉化的 Mod 文件夹")).pack(side=LEFT)
        ttk.Button(row, text="使用选中 Mod", command=self._use_selected_mod_for_ai).pack(side=LEFT, padx=(8, 0))

        action_row = ttk.Frame(top)
        action_row.pack(fill=X, padx=8, pady=(0, 8))
        self._ai_scan_button = ttk.Button(action_row, text="扫描", command=self._scan_ai_target)
        self._ai_scan_button.pack(side=LEFT)
        self._ai_generate_button = ttk.Button(action_row, text="生成中文", command=self._generate_ai_translation)
        self._ai_generate_button.pack(side=LEFT, padx=(8, 0))

        info_box = ttk.LabelFrame(tab, text="汉化状态")
        info_box.pack(fill=BOTH, expand=True, pady=(12, 0))
        self._ai_summary_text = Text(info_box, height=20, wrap="word", borderwidth=0, background="#fffdf8", foreground="#2f2922")
        self._ai_summary_text.pack(fill=BOTH, expand=True, padx=8, pady=8)
        self._ai_summary_text.configure(state="disabled")

    def _build_settings_tab(self) -> None:
        tab = ttk.Frame(self._notebook, padding=12)
        self._notebook.add(tab, text="设置")

        paths_box = ttk.LabelFrame(tab, text="路径")
        paths_box.pack(fill=X)
        self._build_path_field(paths_box, "Mod 库目录", self._library_root_var, "选择 Mod 库目录")
        self._build_path_field(paths_box, "游戏目录", self._game_root_var, "选择游戏目录")
        self._build_path_field(paths_box, "游戏 Mods 目录", self._game_mods_root_var, "选择游戏 Mods 目录")

        ai_box = ttk.LabelFrame(tab, text="AI")
        ai_box.pack(fill=X, pady=(12, 0))

        row = ttk.Frame(ai_box)
        row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Checkbutton(row, text="启用 AI", variable=self._ai_enabled_var).pack(side=LEFT)
        ttk.Checkbutton(row, text="启用汉化功能", variable=self._translation_enabled_var).pack(side=LEFT, padx=(12, 0))

        self._build_option_field(ai_box, "Provider", self._ai_provider_var, [OPENAI_LABEL])
        self._build_text_field(ai_box, "API Key", self._openai_key_var, secret=True)
        self._build_text_field(ai_box, "模型", self._openai_model_var)
        self._build_text_field(ai_box, "Base URL", self._openai_base_url_var)

        import_box = ttk.LabelFrame(tab, text="导入")
        import_box.pack(fill=X, pady=(12, 0))
        self._build_option_field(import_box, "导入策略", self._import_policy_var, list(IMPORT_POLICY_LABELS.values()))

        button_row = ttk.Frame(tab)
        button_row.pack(fill=X, pady=(12, 0))
        self._save_settings_button = ttk.Button(button_row, text="保存设置", command=self._save_settings_action)
        self._save_settings_button.pack(side=LEFT)
        ttk.Label(button_row, text="保存后会写入本地状态文件，并用于导入与 AI 汉化。", foreground="#655748").pack(side=LEFT, padx=(12, 0))

    def _build_log_panel(self, outer: ttk.Frame) -> None:
        log_box = ttk.LabelFrame(outer, text="日志")
        log_box.pack(fill=BOTH, expand=False, pady=(10, 0))
        self._log_text = Text(log_box, height=8, wrap="word", borderwidth=0, background="#fffdf8", foreground="#2f2922")
        self._log_text.pack(fill=BOTH, expand=True, padx=8, pady=8)
        self._log_text.configure(state="disabled")

    def _build_path_field(self, parent: ttk.Widget, label: str, var: StringVar, title: str) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Label(row, text=label).pack(side=LEFT)
        entry = ttk.Entry(row, textvariable=var)
        entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 8))
        ttk.Button(row, text="浏览", command=lambda: self._choose_directory(var, title)).pack(side=LEFT)

    def _build_text_field(self, parent: ttk.Widget, label: str, var: StringVar, secret: bool = False) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=X, padx=8, pady=(4, 4))
        ttk.Label(row, text=label).pack(side=LEFT)
        entry = ttk.Entry(row, textvariable=var, show="*" if secret else "")
        entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

    def _build_option_field(self, parent: ttk.Widget, label: str, var: StringVar, options: list[str]) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=X, padx=8, pady=(4, 4))
        ttk.Label(row, text=label).pack(side=LEFT)
        combo = ttk.Combobox(row, textvariable=var, values=options, state="readonly")
        combo.pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

    def _apply_settings_to_form(self, settings: AppSettings) -> None:
        self._library_root_var.set(str(settings.library_root) if settings.library_root else "")
        self._game_root_var.set(str(settings.game_root) if settings.game_root else "")
        self._game_mods_root_var.set(str(settings.game_mods_root) if settings.game_mods_root else "")
        self._ai_enabled_var.set(settings.ai_enabled)
        self._translation_enabled_var.set(settings.translation_enabled)
        self._ai_provider_var.set(OPENAI_LABEL)
        self._openai_key_var.set(settings.openai_api_key)
        self._openai_model_var.set(settings.openai_model)
        self._openai_base_url_var.set(settings.openai_base_url)
        self._import_policy_var.set(IMPORT_POLICY_LABELS.get(settings.import_policy, IMPORT_POLICY_LABELS["overwrite"]))

    def _collect_settings_from_form(self) -> AppSettings:
        library_root = self._parse_path(self._library_root_var.get())
        game_root = self._parse_path(self._game_root_var.get())
        game_mods_root = self._parse_path(self._game_mods_root_var.get())
        policy = IMPORT_POLICY_VALUES.get(self._import_policy_var.get(), "overwrite")
        provider = "openai"
        return AppSettings(
            library_root=library_root,
            game_root=game_root,
            game_mods_root=game_mods_root,
            ai_enabled=bool(self._ai_enabled_var.get()),
            ai_provider=provider,
            openai_api_key=self._openai_key_var.get().strip(),
            openai_model=self._openai_model_var.get().strip() or "gpt-4o-mini",
            openai_base_url=self._openai_base_url_var.get().strip(),
            translation_enabled=bool(self._translation_enabled_var.get()),
            import_policy=policy,
        )

    def _parse_path(self, value: str) -> Path | None:
        text = value.strip()
        return Path(text).expanduser() if text else None

    def _persist_state(self) -> None:
        save_state(self._settings, self._mods_by_path)

    def _choose_directory(self, var: StringVar, title: str) -> None:
        selected = filedialog.askdirectory(title=title)
        if selected:
            var.set(selected)
            self._sync_button_states()

    def _save_settings_action(self) -> None:
        previous_library = self._settings.library_root
        self._settings = self._collect_settings_from_form()
        self._persist_state()
        self._append_log("Settings saved.")
        self._status_var.set("Settings saved")
        self._sync_button_states()
        if self._settings.library_root and self._settings.library_root != previous_library:
            self._scan_library_action()

    def _scan_library_action(self) -> None:
        self._settings = self._collect_settings_from_form()
        self._persist_state()
        if self._settings.library_root is None:
            messagebox.showwarning("Mod 库目录未设置", "请先在设置页或顶部选择 Mod 库目录。")
            return
        self._start_worker("扫描 Mod 库中...", lambda: self._scan_library_worker(self._settings.library_root, dict(self._mods_by_path)))

    def _scan_library_worker(self, library_root: Path, existing_records: dict[str, ManagedMod]) -> None:
        try:
            records = scan_library(library_root, existing_records)
            self._queue.put(WorkerEvent(kind="library_scan", mods=records, message=f"Scanned {len(records)} mods"))
        except Exception as exc:
            self._queue.put(WorkerEvent(kind="error", message=f"Library scan failed: {exc}"))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _import_enabled_mods(self) -> None:
        self._settings = self._collect_settings_from_form()
        self._persist_state()
        game_mods_root = resolve_game_mods_root(self._settings)
        if game_mods_root is None:
            messagebox.showwarning("Mods 路径未设置", "请先在设置页设置游戏目录或游戏 Mods 目录。")
            return

        enabled_mods = [record for record in self._mods_by_path.values() if record.enabled]
        if not enabled_mods:
            messagebox.showinfo("没有启用项", "当前没有任何启用的 Mod 可导入。")
            return

        policy = self._settings.import_policy
        if policy == "prompt":
            conflicts = [record for record in enabled_mods if (game_mods_root / record.source_path.name).exists()]
            if conflicts:
                answer = messagebox.askyesnocancel(
                    "导入冲突",
                    f"{len(conflicts)} 个目标文件夹已存在。选择“是”覆盖，选择“否”跳过，取消则中止。",
                )
                if answer is None:
                    return
                policy = "overwrite" if answer else "skip"
            else:
                policy = "overwrite"

        self._start_worker(
            "导入启用的 Mod 中...",
            lambda: self._import_worker(enabled_mods, game_mods_root, policy),
        )

    def _import_worker(self, enabled_mods: list[ManagedMod], game_mods_root: Path, policy: str) -> None:
        try:
            report = deploy_enabled_mods(enabled_mods, game_mods_root, policy=policy)
            summary = (
                f"Import complete: copied {len(report.copied)}, skipped {len(report.skipped)}, failed {len(report.failed)}"
            )
            self._queue.put(WorkerEvent(kind="import", import_report=report, message=summary))
        except Exception as exc:
            self._queue.put(WorkerEvent(kind="error", message=f"Import failed: {exc}"))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _use_selected_mod_for_ai(self) -> None:
        record = self._selected_record()
        if record is None:
            messagebox.showinfo("未选择 Mod", "请先在 Mod 列表中选择一个 Mod。")
            return
        self._ai_folder_var.set(str(record.source_path))
        self._current_analysis = record.analysis
        if record.analysis is not None:
            self._render_analysis(record.analysis, target="ai")
        else:
            self._set_text(self._ai_summary_text, self._render_record_summary(record))
        self._sync_button_states()

    def _scan_ai_target(self) -> None:
        target = self._parse_path(self._ai_folder_var.get())
        if target is None:
            messagebox.showwarning("未选择文件夹", "请先选择要汉化的 Mod 文件夹。")
            return
        self._start_worker("扫描汉化目标中...", lambda: self._scan_ai_worker(target))

    def _scan_ai_worker(self, folder: Path) -> None:
        try:
            analysis = scan_mod(folder)
            self._queue.put(WorkerEvent(kind="analysis", analysis=analysis, message="AI scan complete"))
        except Exception as exc:
            self._queue.put(WorkerEvent(kind="error", message=f"AI scan failed: {exc}"))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _generate_ai_translation(self) -> None:
        self._settings = self._collect_settings_from_form()
        if not self._settings.ai_enabled or not self._settings.translation_enabled:
            messagebox.showwarning("AI 未启用", "请先在设置页启用 AI 和汉化功能。")
            return
        target = self._parse_path(self._ai_folder_var.get())
        if target is None:
            messagebox.showwarning("未选择文件夹", "请先选择要汉化的 Mod 文件夹。")
            return
        self._persist_state()
        self._start_worker("调用 AI 生成中文中...", lambda: self._translate_worker(target))

    def _translate_worker(self, folder: Path) -> None:
        try:
            analysis = scan_mod(folder)
            self._queue.put(WorkerEvent(kind="analysis", analysis=analysis, message="Scanned target for translation"))
            result = translate_with_openai(
                analysis,
                api_key=self._settings.openai_api_key or None,
                model=self._settings.openai_model or None,
                base_url=self._settings.openai_base_url or None,
            )
            written = write_json_file(result.output_path, result.payload, source_payload=self._build_validation_source(analysis))
            self._queue.put(WorkerEvent(kind="log", message=f"Wrote {written}"))
            self._queue.put(WorkerEvent(kind="log", message=f"Source files: {', '.join(str(path) for path in result.source_paths)}"))
            self._queue.put(WorkerEvent(kind="analysis", analysis=analysis, message=f"Translation complete: {written}"))
        except Exception as exc:
            self._queue.put(WorkerEvent(kind="error", message=f"Translation failed: {exc}"))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _build_validation_source(self, analysis: ModAnalysis):
        if not analysis.translatable_sources:
            return None
        if len(analysis.translatable_sources) == 1 and analysis.default_layout == "flat":
            return self._load_json(analysis.translatable_sources[0])
        if analysis.default_layout == "tree" and analysis.default_locale_root is not None:
            payload: dict[str, object] = {}
            for path in analysis.translatable_sources:
                payload[path.relative_to(analysis.default_locale_root).as_posix()] = self._load_json(path)
            return payload
        payload: dict[str, object] = {}
        for path in analysis.translatable_sources:
            payload[path.name] = self._load_json(path)
        return payload

    def _load_json(self, path: Path):
        import json

        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _start_worker(self, status: str, target: Callable[[], None]) -> None:
        if self._worker_running:
            self._append_log("A task is already running.")
            return
        self._worker_running = True
        self._status_var.set(status)
        self._sync_button_states()
        thread = threading.Thread(target=target, daemon=True)
        thread.start()

    def _poll_queue(self) -> None:
        processed = False
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            processed = True
            self._handle_event(event)
        if processed:
            self.root.update_idletasks()
        self.root.after(100, self._poll_queue)

    def _handle_event(self, event: WorkerEvent) -> None:
        if event.kind == "log":
            self._append_log(event.message)
            self._status_var.set(event.message)
        elif event.kind == "library_scan":
            self._mods_by_path = {str(record.source_path.resolve()): record for record in event.mods}
            self._persist_state()
            self._refresh_mod_tree()
            self._append_log(event.message)
            self._status_var.set(event.message or "Library scan complete")
        elif event.kind == "analysis" and event.analysis is not None:
            self._current_analysis = event.analysis
            self._render_analysis(event.analysis, target="ai")
            if event.message:
                self._append_log(event.message)
                self._status_var.set(event.message)
        elif event.kind == "import" and event.import_report is not None:
            self._append_log(event.message)
            for path in event.import_report.copied:
                self._append_log(f"Copied: {path}")
            for path in event.import_report.skipped:
                self._append_log(f"Skipped: {path}")
            for path, error in event.import_report.failed:
                self._append_log(f"Failed: {path} -> {error}")
            self._status_var.set(event.message)
        elif event.kind == "error":
            self._append_log(event.message)
            self._status_var.set(event.message)
        elif event.kind == "done":
            self._worker_running = False
            self._sync_button_states()
            if self._status_var.get() in {"Scanning Mod 库中...", "扫描汉化目标中...", "调用 AI 生成中文中...", "导入启用的 Mod 中..."}:
                self._status_var.set("Idle")

    def _refresh_mod_tree(self) -> None:
        self._mods_tree.delete(*self._mods_tree.get_children())
        selected = self._current_selected_path
        records = list(self._mods_by_path.values())
        records = [record for record in records if self._matches_filter(record)]
        records.sort(key=self._sort_key, reverse=self._sort_reverse)
        for record in records:
            iid = str(record.source_path.resolve())
            self._mods_tree.insert(
                "",
                END,
                iid=iid,
                values=(
                    "✓" if record.enabled else "",
                    record.display_name or record.source_path.name,
                    record.mod_type,
                    record.version or "",
                    record.author or "",
                    record.translation_status,
                    str(record.source_path),
                ),
            )
        if selected and self._mods_tree.exists(selected):
            self._mods_tree.selection_set(selected)
            self._mods_tree.see(selected)
        self._update_library_summary()
        self._update_detail_panel()
        self._sync_button_states()

    def _matches_filter(self, record: ManagedMod) -> bool:
        query = self._search_var.get().strip().lower()
        if not query:
            return True
        haystack = " ".join(
            [
                record.display_name,
                record.author or "",
                record.version or "",
                record.unique_id or "",
                str(record.source_path),
                record.mod_type,
                record.translation_status,
                " ".join(record.tags),
                record.notes,
            ]
        ).lower()
        return query in haystack

    def _sort_key(self, record: ManagedMod):
        columns = {
            "enabled": (0 if record.enabled else 1, record.display_name.lower()),
            "display_name": record.display_name.lower(),
            "mod_type": record.mod_type,
            "version": record.version or "",
            "author": record.author or "",
            "translation_status": record.translation_status,
            "path": str(record.source_path).lower(),
        }
        return columns.get(self._sort_column, record.display_name.lower())

    def _toggle_sort(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self._refresh_mod_tree()

    def _selected_record(self, populate_metadata: bool = False) -> ManagedMod | None:
        selection = self._mods_tree.selection()
        if not selection:
            return None
        key = selection[0]
        self._current_selected_path = key
        record = self._mods_by_path.get(key)
        if record is not None and populate_metadata:
            self._fill_metadata_fields(record)
        return record

    def _on_tree_select(self, _event: object) -> None:
        record = self._selected_record(populate_metadata=True)
        if record is None:
            self._clear_metadata_fields()
            self._update_detail_panel()
            return
        self._ai_folder_var.set(str(record.source_path))
        if record.analysis is not None:
            self._current_analysis = record.analysis
            self._render_analysis(record.analysis, target="library")
        else:
            self._set_text(self._detail_text, self._render_record_summary(record))
        self._sync_button_states()

    def _set_selected_enabled(self, enabled: bool) -> None:
        record = self._selected_record()
        if record is None:
            messagebox.showinfo("未选择 Mod", "请先选择一个 Mod。")
            return
        record.enabled = enabled
        self._persist_state()
        self._refresh_mod_tree()

    def _toggle_selected_mod(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        self._set_selected_enabled(not record.enabled)

    def _fill_metadata_fields(self, record: ManagedMod) -> None:
        self._tags_var.set(", ".join(record.tags))
        self._notes_text.configure(state="normal")
        self._notes_text.delete("1.0", END)
        self._notes_text.insert("1.0", record.notes)
        self._notes_text.configure(state="normal")

    def _clear_metadata_fields(self) -> None:
        self._tags_var.set("")
        self._notes_text.configure(state="normal")
        self._notes_text.delete("1.0", END)
        self._notes_text.configure(state="normal")

    def _apply_selected_metadata(self) -> None:
        record = self._selected_record()
        if record is None:
            messagebox.showinfo("未选择 Mod", "请先选择一个 Mod。")
            return
        tags = [item.strip() for item in re.split(r"[;,]", self._tags_var.get()) if item.strip()]
        notes = self._notes_text.get("1.0", END).strip()
        record.tags = tags
        record.notes = notes
        self._persist_state()
        self._refresh_mod_tree()
        self._append_log(f"Updated metadata for {record.display_name or record.source_path.name}")

    def _update_library_summary(self) -> None:
        total = len(self._mods_by_path)
        enabled = sum(1 for record in self._mods_by_path.values() if record.enabled)
        library_root = self._settings.library_root or self._parse_path(self._library_root_var.get())
        summary = [f"总数：{total}", f"启用：{enabled}"]
        if library_root is not None:
            summary.append(f"库目录：{library_root}")
        if hasattr(self, "_library_summary_var"):
            self._library_summary_var.set(" | ".join(summary))

    def _update_detail_panel(self) -> None:
        record = self._selected_record()
        if record is None:
            self._set_text(self._detail_text, "选择一个 Mod 查看详情。")
            self._clear_metadata_fields()
            return
        self._set_text(self._detail_text, self._render_record_summary(record))

    def _render_record_summary(self, record: ManagedMod) -> str:
        lines = [
            f"Folder: {record.source_path}",
            f"Enabled: {'yes' if record.enabled else 'no'}", 
            f"Name: {record.display_name or record.source_path.name}",
            f"Author: {record.author or 'n/a'}",
            f"Version: {record.version or 'n/a'}",
            f"Type: {record.mod_type}",
            f"UniqueID: {record.unique_id or 'n/a'}",
            f"Translation status: {record.translation_status}",
            f"Chinese present: {'yes' if record.has_chinese else 'no'}",
            f"Missing keys: {record.missing_keys_count}",
            f"Manifest: {'found' if record.has_manifest else 'missing'}",
            f"Manifest path: {record.manifest_path or 'n/a'}",
            f"Tags: {', '.join(record.tags) or 'n/a'}",
            f"Notes: {record.notes or 'n/a'}",
        ]
        if record.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in record.warnings)
        if record.analysis is not None and record.analysis.manifest_hints:
            lines.append("Manifest hints: " + ", ".join(record.analysis.manifest_hints))
        return "\n".join(lines)

    def _render_analysis(self, analysis: ModAnalysis, target: str) -> None:
        lines = [
            f"Folder: {analysis.mod_path}",
            f"Mod name: {analysis.mod_name}",
            f"Manifest: {'found' if analysis.has_manifest else 'missing'}",
            f"Manifest path: {analysis.manifest_path or 'n/a'}",
            f"Mod type: {analysis.mod_type}",
            f"Translation status: {analysis.translation_status}",
            f"Chinese present: {'yes' if analysis.has_chinese else 'no'}",
            f"Default locale: {analysis.default_locale_path or 'n/a'}",
            f"Chinese locale: {analysis.zh_locale_path or 'n/a'}",
            f"Missing keys: {analysis.missing_keys_count}",
            f"Translatable sources: {', '.join(str(path) for path in analysis.translatable_sources) or 'n/a'}",
        ]
        if analysis.manifest is not None:
            lines.extend(
                [
                    f"Author: {analysis.manifest.author or 'n/a'}",
                    f"Version: {analysis.manifest.version or 'n/a'}",
                    f"UniqueID: {analysis.manifest.unique_id or 'n/a'}",
                ]
            )
        if analysis.manifest_hints:
            lines.append("Manifest hints: " + ", ".join(analysis.manifest_hints))
        if analysis.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in analysis.warnings)
        if target == "ai":
            self._set_text(self._ai_summary_text, "\n".join(lines))
        else:
            self._set_text(self._detail_text, "\n".join(lines))

    def _sync_button_states(self) -> None:
        has_selection = self._mods_tree.selection() != ()
        has_library = self._parse_path(self._library_root_var.get()) is not None
        has_ai_target = self._parse_path(self._ai_folder_var.get()) is not None
        ai_ready = self._ai_enabled_var.get() and self._translation_enabled_var.get() and has_ai_target
        import_ready = resolve_game_mods_root(self._collect_settings_from_form()) is not None and any(
            record.enabled for record in self._mods_by_path.values()
        )

        action_state = "disabled" if self._worker_running else "normal"
        record_state = "disabled" if self._worker_running or not has_selection else "normal"

        self._rescan_button.configure(state=action_state if has_library else "disabled")
        self._import_button.configure(state=action_state if import_ready else "disabled")
        self._enable_button.configure(state=record_state)
        self._disable_button.configure(state=record_state)
        self._toggle_button.configure(state=record_state)
        self._apply_metadata_button.configure(state=record_state)
        self._ai_scan_button.configure(state=action_state if has_ai_target else "disabled")
        self._ai_generate_button.configure(state=action_state if ai_ready else "disabled")
        self._save_settings_button.configure(state=action_state)

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self._log_text.configure(state="normal")
        self._log_text.insert(END, f"[{timestamp}] {message}\n")
        self._log_text.see(END)
        self._log_text.configure(state="disabled")

    def _set_text(self, widget: Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")


TranslationApp = ModManagerApp
