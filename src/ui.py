from __future__ import annotations

import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk, Text

from .manager import deploy_enabled_mods, resolve_game_mods_root, scan_library
from .models import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    AppSettings,
    ManagedMod,
    ModAnalysis,
    WorkerEvent,
)
from .nexus import NexusService
from .nexus_auth import NexusAuthSession
from .scanner import scan_mod
from .storage import load_state, save_state
from .translator import probe_openai_connection, translate_with_openai
from .writers import write_json_file

IMPORT_POLICY_LABELS = {
    "overwrite": "覆盖",
    "skip": "跳过",
    "prompt": "询问",
}
IMPORT_POLICY_VALUES = {label: value for value, label in IMPORT_POLICY_LABELS.items()}
TRANSLATION_STATUS_LABELS = {
    "translated": "已汉化",
    "partial": "部分汉化",
    "not_translated": "未汉化",
    "unknown": "未知",
}
MOD_TYPE_LABELS = {
    "smapi": "SMAPI 模组",
    "content_pack": "内容包",
    "unknown": "未知",
}
NEXUS_UPDATE_STATUS_LABELS = {
    "unknown": "未知",
    "no_source": "无来源",
    "up_to_date": "已最新",
    "outdated": "可更新",
    "failed": "失败",
    "installed": "已安装",
}


def _checkbox_symbol(value: bool) -> str:
    """把布尔状态渲染成复选框样式的文本符号。"""
    return "☑" if value else "☐"


def _boolean_label(value: bool) -> str:
    """把布尔值渲染成中文标签。"""
    return "是" if value else "否"


def _localized_enabled_state(value: bool) -> str:
    """把启用状态渲染成中文文字。"""
    return "启用" if value else "禁用"


def _localized_translation_status(value: str) -> str:
    """把汉化状态转换成中文显示文本。"""
    return TRANSLATION_STATUS_LABELS.get(value, value)


def _localized_mod_type(value: str) -> str:
    """把 Mod 类型转换成中文显示文本。"""
    return MOD_TYPE_LABELS.get(value, value)


def _localized_nexus_status(value: str) -> str:
    """把 Nexus 更新状态转换成中文显示文本。"""
    return NEXUS_UPDATE_STATUS_LABELS.get(value, value)


class AIOptionsDialog:
    def __init__(self, master: Tk, enabled_count: int, model: str, api_key_present: bool) -> None:
        """弹出汉化选项对话框，让用户选择批处理模式。"""
        self.result: str | None = None
        self.window = Toplevel(master)
        self.window.title("汉化选项")
        self.window.resizable(False, False)
        self.window.transient(master)
        self.window.grab_set()

        self._mode_var = StringVar(value="incremental")
        outer = ttk.Frame(self.window, padding=14)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(outer, text=f"将处理 {enabled_count} 个已启用的 Mod。", foreground="#3a332b").pack(anchor="w")
        ttk.Label(
            outer,
            text=f"AI: 模型 {model} / {'API Key 已填写' if api_key_present else '将回退环境变量'}",
            foreground="#655748",
        ).pack(anchor="w", pady=(4, 10))

        mode_box = ttk.LabelFrame(outer, text="汉化模式")
        mode_box.pack(fill=X)
        ttk.Radiobutton(mode_box, text="仅补全缺失", value="incremental", variable=self._mode_var).pack(anchor="w", padx=10, pady=(8, 2))
        ttk.Radiobutton(mode_box, text="强制汉化", value="force", variable=self._mode_var).pack(anchor="w", padx=10, pady=(0, 8))

        ttk.Label(outer, text="输出仍采用安全生成文件（zh.json），不会直接覆盖原文件。", foreground="#655748").pack(
            anchor="w", pady=(10, 0)
        )

        button_row = ttk.Frame(outer)
        button_row.pack(fill=X, pady=(14, 0))
        ttk.Button(button_row, text="开始", command=self._start).pack(side=LEFT)
        ttk.Button(button_row, text="取消", command=self._cancel).pack(side=LEFT, padx=(8, 0))

        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.wait_visibility()
        self.window.focus_set()

    def show(self) -> str | None:
        """阻塞等待对话框关闭，并返回用户选择的模式。"""
        self.window.wait_window()
        return self.result

    def _start(self) -> None:
        """记录用户选择并关闭对话框。"""
        self.result = self._mode_var.get()
        self.window.destroy()

    def _cancel(self) -> None:
        """取消并关闭对话框。"""
        self.result = None
        self.window.destroy()


class ModManagerApp:
    def __init__(self) -> None:
        """初始化主窗口、状态变量和数据源。"""
        self.root = Tk()
        self.root.title("Stardew Valley Mod Manager")
        self.root.geometry("1280x840")
        self.root.minsize(1020, 700)

        self._queue: queue.Queue[WorkerEvent] = queue.Queue()
        self._worker_running = False
        self._settings, loaded_mods = load_state()
        self._mods_by_path: dict[str, ManagedMod] = dict(loaded_mods)
        self._current_selected_path: str | None = None

        self._search_var = StringVar(value="")
        self._library_root_var = StringVar(value="")
        self._game_root_var = StringVar(value="")
        self._game_mods_root_var = StringVar(value="")
        self._openai_key_var = StringVar(value="")
        self._nexus_api_key_var = StringVar(value="")
        self._openai_model_var = StringVar(value=DEFAULT_OPENAI_MODEL)
        self._openai_base_url_var = StringVar(value=DEFAULT_OPENAI_BASE_URL)
        self._import_policy_var = StringVar(value=IMPORT_POLICY_LABELS["overwrite"])
        self._ai_enabled_var = BooleanVar(value=True)
        self._translation_enabled_var = BooleanVar(value=True)
        self._tags_var = StringVar(value="")
        self._library_summary_var = StringVar(value="")
        self._status_var = StringVar(value="Idle")
        self._progress_var = StringVar(value="0%")
        self._summary_var = StringVar(value="")
        self._selected_count_var = StringVar(value="已选中 0 个 | 已勾选 0 个")

        self._sort_column = "display_name"
        self._sort_reverse = False

        self._setup_style()
        self._build_ui()
        self._apply_settings_to_form(self._settings)
        self._search_var.trace_add("write", lambda *_: self._refresh_mod_tree())
        self._refresh_mod_tree()
        self._sync_button_states()
        self._poll_queue()

        if self._settings.library_root and not self._mods_by_path:
            self.root.after(250, self._scan_library_action)

    def run(self) -> None:
        """进入 Tk 主事件循环。"""
        self.root.mainloop()

    def _setup_style(self) -> None:
        """配置基础主题与通用控件样式。"""
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
        style.configure("TEntry", padding=(6, 4))

    def _build_ui(self) -> None:
        """搭建整个主界面，包括页签、日志和进度区。"""
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=BOTH, expand=True)

        self._notebook = ttk.Notebook(outer)
        self._notebook.pack(fill=BOTH, expand=True)

        self._build_management_tab(self._notebook)
        self._build_settings_tab(self._notebook)
        self._build_log_panel(outer)
        self._build_progress_panel(outer)

        footer = ttk.Frame(outer)
        footer.pack(fill=X, pady=(8, 0))
        ttk.Label(footer, textvariable=self._status_var).pack(side=LEFT)

    def _build_management_tab(self, notebook: ttk.Notebook) -> None:
        """构建 Mod 管理页，负责列表、详情和批量操作入口。"""
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Mod 管理")

        top = ttk.LabelFrame(tab, text="Mod 库")
        top.pack(fill=X)

        row = ttk.Frame(top)
        row.pack(fill=X, padx=8, pady=(8, 4))
        self._scan_button = ttk.Button(row, text="重新扫描", command=self._scan_library_action)
        self._scan_button.pack(side=LEFT)

        search_row = ttk.Frame(top)
        search_row.pack(fill=X, padx=8, pady=(0, 8))
        ttk.Label(search_row, text="搜索").pack(side=LEFT)
        ttk.Entry(search_row, textvariable=self._search_var).pack(side=LEFT, fill=X, expand=True, padx=(8, 0))
        ttk.Label(top, textvariable=self._library_summary_var, foreground="#655748").pack(anchor="w", padx=8, pady=(0, 8))

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill=BOTH, expand=True, pady=(12, 0))

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        list_box = ttk.LabelFrame(left, text="Mod 列表")
        list_box.pack(fill=BOTH, expand=True)

        tree_frame = ttk.Frame(list_box)
        tree_frame.pack(fill=BOTH, expand=True, padx=8, pady=(8, 0))

        # Use grid inside the frame for stable scrollbar alignment across themes/DPI.
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = (
            "checked",
            "enabled",
            "display_name",
            "mod_type",
            "version",
            "nexus_latest_version",
            "nexus_update_status",
            "author",
            "translation_status",
            "path",
        )
        self._mods_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "checked": "勾选",
            "enabled": "启用",
            "display_name": "名称",
            "mod_type": "类型",
            "version": "版本",
            "nexus_latest_version": "远端版本",
            "nexus_update_status": "更新状态",
            "author": "作者",
            "translation_status": "汉化状态",
            "path": "路径",
        }
        widths = {
            "checked": 70,
            "enabled": 70,
            "display_name": 180,
            "mod_type": 100,
            "version": 90,
            "nexus_latest_version": 90,
            "nexus_update_status": 90,
            "author": 150,
            "translation_status": 110,
            "path": 320,
        }
        for column in columns:
            self._mods_tree.heading(column, text=headings[column], command=lambda c=column: self._toggle_sort(c))
            self._mods_tree.column(column, width=widths[column], anchor="w", stretch=True)

        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._mods_tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._mods_tree.xview)
        self._mods_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self._mods_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        ttk.Frame(tree_frame).grid(row=1, column=1, sticky="nsew")

        self._mods_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._mods_tree.bind("<Button-1>", self._on_tree_click)

        # 把底部批量操作和提示放进列表卡片里，避免在窗口高度不足时被挤出可视区域。
        action_row = ttk.Frame(list_box)
        action_row.pack(fill=X, padx=8, pady=(10, 0))
        ttk.Label(action_row, textvariable=self._selected_count_var, foreground="#655748").pack(side=LEFT, padx=(0, 8))
        self._enable_button = ttk.Button(action_row, text="启用勾选", command=lambda: self._set_selected_enabled(True))
        self._enable_button.pack(side=LEFT)
        self._disable_button = ttk.Button(action_row, text="停用勾选", command=lambda: self._set_selected_enabled(False))
        self._disable_button.pack(side=LEFT, padx=(8, 0))
        self._select_all_button = ttk.Button(action_row, text="全选勾选", command=self._select_all_mods)
        self._select_all_button.pack(side=LEFT, padx=(8, 0))
        self._clear_selection_button = ttk.Button(action_row, text="清空勾选", command=self._clear_selection)
        self._clear_selection_button.pack(side=LEFT, padx=(8, 0))
        self._invert_selection_button = ttk.Button(action_row, text="反选勾选", command=self._invert_selection)
        self._invert_selection_button.pack(side=LEFT, padx=(8, 0))
        self._check_translation_button = ttk.Button(action_row, text="检查汉化情况", command=self._check_translation_action)
        self._check_translation_button.pack(side=LEFT, padx=(8, 0))
        self._check_nexus_updates_button = ttk.Button(action_row, text="检查 Nexus 更新", command=self._check_nexus_updates_action)
        self._check_nexus_updates_button.pack(side=LEFT, padx=(8, 0))
        self._download_nexus_updates_button = ttk.Button(action_row, text="下载并安装更新", command=self._download_nexus_updates_action)
        self._download_nexus_updates_button.pack(side=LEFT, padx=(8, 0))
        self._translate_button = ttk.Button(action_row, text="汉化", command=self._translate_enabled_action)
        self._translate_button.pack(side=LEFT, padx=(8, 0))
        self._import_enabled_button = ttk.Button(action_row, text="导入启用项", command=self._import_enabled_action)
        self._import_enabled_button.pack(side=LEFT, padx=(8, 0))

        ttk.Label(
            list_box,
            text="提示：点击“勾选”列批量选择，点击“启用”列切换状态；汉化/检查优先处理勾选项；导入只导入已启用。",
            foreground="#655748",
        ).pack(anchor="w", padx=8, pady=(8, 8))

        detail_box = ttk.LabelFrame(right, text="详情")
        detail_box.pack(fill=BOTH, expand=True)
        self._detail_text = Text(detail_box, height=16, wrap="word", borderwidth=0, background="#fffdf8", foreground="#2f2922")
        self._detail_text.pack(fill=BOTH, expand=True, padx=8, pady=(8, 4))
        self._detail_text.configure(state="disabled")

        meta_box = ttk.LabelFrame(right, text="标签 / 备注")
        meta_box.pack(fill=BOTH, expand=False, pady=(10, 0))
        meta_row = ttk.Frame(meta_box)
        meta_row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Label(meta_row, text="标签").pack(side=LEFT)
        ttk.Entry(meta_row, textvariable=self._tags_var).pack(side=LEFT, fill=X, expand=True, padx=(8, 0))
        ttk.Label(meta_box, text="备注").pack(anchor="w", padx=8, pady=(4, 0))
        self._notes_text = Text(meta_box, height=6, wrap="word", borderwidth=0, background="#fffdf8", foreground="#2f2922")
        self._notes_text.pack(fill=BOTH, expand=True, padx=8, pady=(4, 8))

        meta_button_row = ttk.Frame(right)
        meta_button_row.pack(fill=X, pady=(8, 0))
        self._save_meta_button = ttk.Button(meta_button_row, text="保存备注", command=self._save_selected_metadata)
        self._save_meta_button.pack(side=LEFT)

    def _build_settings_tab(self, notebook: ttk.Notebook) -> None:
        """构建设置页，集中放置路径、AI 和导入策略配置。"""
        tab = ttk.Frame(notebook, padding=12)
        self._settings_tab = tab
        notebook.add(tab, text="设置")

        paths_box = ttk.LabelFrame(tab, text="路径")
        paths_box.pack(fill=X)
        self._add_path_row(paths_box, "Mod 库目录", self._library_root_var, "选择 Mod 库目录")
        self._add_path_row(paths_box, "游戏目录", self._game_root_var, "选择游戏目录")
        self._add_path_row(paths_box, "游戏 Mods 目录", self._game_mods_root_var, "选择游戏 Mods 目录")

        ai_box = ttk.LabelFrame(tab, text="AI")
        ai_box.pack(fill=X, pady=(12, 0))
        ai_row = ttk.Frame(ai_box)
        ai_row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Checkbutton(ai_row, text="启用 AI", variable=self._ai_enabled_var).pack(side=LEFT)
        ttk.Checkbutton(ai_row, text="启用汉化功能", variable=self._translation_enabled_var).pack(side=LEFT, padx=(12, 0))

        self._nexus_api_key_button = self._add_text_row_with_action(
            ai_box,
            "Nexus API Key",
            self._nexus_api_key_var,
            "获取 API Key",
            self._request_nexus_api_key_action,
            secret=True,
        )
        ttk.Label(ai_box, text="点击后会打开 Nexus 登录页，并在完成授权后自动回填 API Key。", foreground="#655748").pack(
            anchor="w", padx=8, pady=(0, 4)
        )
        self._add_text_row(ai_box, "API Key", self._openai_key_var, secret=True)
        self._add_text_row(ai_box, "模型", self._openai_model_var)
        self._add_text_row(ai_box, "Base URL", self._openai_base_url_var)

        test_row = ttk.Frame(ai_box)
        test_row.pack(fill=X, padx=8, pady=(4, 8))
        self._test_ai_button = ttk.Button(test_row, text="测试连接", command=self._test_openai_action)
        self._test_ai_button.pack(side=LEFT)
        ttk.Label(test_row, text="会用当前填写的 API Key、模型和 Base URL 发起一次最小请求。", foreground="#655748").pack(
            side=LEFT, padx=(12, 0)
        )

        import_box = ttk.LabelFrame(tab, text="导入")
        import_box.pack(fill=X, pady=(12, 0))
        self._add_option_row(import_box, "导入策略", self._import_policy_var, list(IMPORT_POLICY_LABELS.values()))

        button_row = ttk.Frame(tab)
        button_row.pack(fill=X, pady=(12, 0))
        self._save_settings_button = ttk.Button(button_row, text="保存设置", command=self._save_settings_action)
        self._save_settings_button.pack(side=LEFT)
        ttk.Label(button_row, text="保存后会写入本地状态文件。", foreground="#655748").pack(side=LEFT, padx=(12, 0))

    def _build_log_panel(self, outer: ttk.Frame) -> None:
        """构建底部日志面板，用于显示后台任务输出。"""
        log_box = ttk.LabelFrame(outer, text="日志")
        log_box.pack(fill=BOTH, expand=False, pady=(10, 0))
        self._log_text = Text(log_box, height=8, wrap="word", borderwidth=0, background="#fffdf8", foreground="#2f2922")
        self._log_text.pack(fill=BOTH, expand=True, padx=8, pady=8)
        self._log_text.configure(state="disabled")

    def _build_progress_panel(self, outer: ttk.Frame) -> None:
        """构建底部进度条和汇总信息区域。"""
        panel = ttk.Frame(outer)
        panel.pack(fill=X, pady=(8, 0))
        self._progress_bar = ttk.Progressbar(panel, mode="determinate", maximum=100)
        self._progress_bar.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(panel, textvariable=self._progress_var, width=8, anchor="e").pack(side=LEFT, padx=(8, 0))
        ttk.Label(panel, textvariable=self._summary_var, foreground="#655748").pack(side=LEFT, padx=(12, 0))

    def _add_path_row(self, parent: ttk.Widget, label: str, var: StringVar, title: str) -> None:
        """添加一个路径输入行和浏览按钮。"""
        row = ttk.Frame(parent)
        row.pack(fill=X, padx=8, pady=(8, 4))
        ttk.Label(row, text=label).pack(side=LEFT)
        ttk.Entry(row, textvariable=var).pack(side=LEFT, fill=X, expand=True, padx=(8, 8))
        ttk.Button(row, text="浏览", command=lambda: self._choose_directory(var, title)).pack(side=LEFT)

    def _add_text_row(self, parent: ttk.Widget, label: str, var: StringVar, secret: bool = False) -> None:
        """添加一个普通文本输入行，secret=True 时以密码样式显示。"""
        row = ttk.Frame(parent)
        row.pack(fill=X, padx=8, pady=(4, 4))
        ttk.Label(row, text=label).pack(side=LEFT)
        ttk.Entry(row, textvariable=var, show="*" if secret else "").pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

    def _add_text_row_with_action(
        self,
        parent: ttk.Widget,
        label: str,
        var: StringVar,
        button_text: str,
        command: Callable[[], None],
        secret: bool = False,
    ) -> ttk.Button:
        """添加一个带操作按钮的文本输入行。"""
        row = ttk.Frame(parent)
        row.pack(fill=X, padx=8, pady=(4, 4))
        ttk.Label(row, text=label).pack(side=LEFT)
        ttk.Entry(row, textvariable=var, show="*" if secret else "").pack(side=LEFT, fill=X, expand=True, padx=(8, 8))
        button = ttk.Button(row, text=button_text, command=command)
        button.pack(side=LEFT)
        return button

    def _add_option_row(self, parent: ttk.Widget, label: str, var: StringVar, values: list[str]) -> None:
        """添加一个下拉选项行。"""
        row = ttk.Frame(parent)
        row.pack(fill=X, padx=8, pady=(4, 4))
        ttk.Label(row, text=label).pack(side=LEFT)
        ttk.Combobox(row, textvariable=var, values=values, state="readonly").pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

    def _apply_settings_to_form(self, settings: AppSettings) -> None:
        """把持久化设置回填到界面控件。"""
        self._library_root_var.set(str(settings.library_root) if settings.library_root else "")
        self._game_root_var.set(str(settings.game_root) if settings.game_root else "")
        self._game_mods_root_var.set(str(settings.game_mods_root) if settings.game_mods_root else "")
        self._ai_enabled_var.set(settings.ai_enabled)
        self._translation_enabled_var.set(settings.translation_enabled)
        self._nexus_api_key_var.set(settings.nexus_api_key)
        self._openai_key_var.set(settings.openai_api_key)
        self._openai_model_var.set(settings.openai_model)
        self._openai_base_url_var.set(settings.openai_base_url)
        self._import_policy_var.set(IMPORT_POLICY_LABELS.get(settings.import_policy, IMPORT_POLICY_LABELS["overwrite"]))

    def _collect_settings_from_form(self) -> AppSettings:
        """从当前表单读取设置，并组装成 AppSettings 对象。"""
        return AppSettings(
            library_root=self._parse_path(self._library_root_var.get()),
            game_root=self._parse_path(self._game_root_var.get()),
            game_mods_root=self._parse_path(self._game_mods_root_var.get()),
            ai_enabled=bool(self._ai_enabled_var.get()),
            nexus_api_key=self._nexus_api_key_var.get().strip(),
            openai_api_key=self._openai_key_var.get().strip(),
            openai_model=self._openai_model_var.get().strip() or DEFAULT_OPENAI_MODEL,
            openai_base_url=self._openai_base_url_var.get().strip(),
            translation_enabled=bool(self._translation_enabled_var.get()),
            import_policy=IMPORT_POLICY_VALUES.get(self._import_policy_var.get(), "overwrite"),
        )

    def _parse_path(self, value: str) -> Path | None:
        """把输入框文本转换成 Path，空字符串则返回 None。"""
        text = value.strip()
        return Path(text).expanduser() if text else None

    def _choose_directory(self, var: StringVar, title: str) -> None:
        """弹出目录选择框，并回填到对应输入框。"""
        selected = filedialog.askdirectory(title=title)
        if selected:
            var.set(selected)
            self._sync_button_states()

    def _open_settings_tab(self) -> None:
        """切换到设置页，方便用户补全路径或 AI 配置。"""
        tab = getattr(self, "_settings_tab", None)
        notebook = getattr(self, "_notebook", None)
        if notebook is None or tab is None:
            return
        notebook.select(tab)

    def _persist_state(self) -> None:
        """把当前设置和 Mod 记录立即写回本地状态文件。"""
        self._settings = self._collect_settings_from_form()
        save_state(self._settings, self._mods_by_path)

    def _save_settings_action(self) -> None:
        """保存当前设置，并在库目录变化时触发重扫。"""
        previous_library = self._settings.library_root
        self._settings = self._collect_settings_from_form()
        self._persist_state()
        self._append_log("Settings saved.")
        self._status_var.set("Settings saved")
        self._sync_button_states()
        if self._settings.library_root and self._settings.library_root != previous_library:
            self._scan_library_action()

    def _scan_library_action(self) -> None:
        """开始扫描 Mod 库，若未配置目录则提示用户先去设置页。"""
        self._settings = self._collect_settings_from_form()
        self._persist_state()
        if self._settings.library_root is None:
            answer = messagebox.askyesno("Mod 库目录未设置", "请先在设置页设置 Mod 库目录。现在打开设置吗？")
            if answer:
                self._open_settings_tab()
            return
        self._start_worker("扫描 Mod 库中...", lambda: self._scan_library_worker(self._settings.library_root, dict(self._mods_by_path)))

    def _scan_library_worker(self, library_root: Path, existing_records: dict[str, ManagedMod]) -> None:
        """在线程中扫描 Mod 库，并把结果推送回 UI 队列。"""
        try:
            records = scan_library(library_root, existing_records)
            self._queue.put(WorkerEvent(kind="library_scan", mods=records, message=f"已扫描 {len(records)} 个 Mod"))
        except Exception as exc:
            self._queue.put(WorkerEvent(kind="error", message=f"Library scan failed: {exc}"))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _refresh_library_from_event(self, records: list[ManagedMod]) -> None:
        """用扫描结果刷新本地缓存，并重新保存状态。"""
        self._mods_by_path = {str(record.source_path.resolve()): record for record in records}
        self._persist_state()
        self._refresh_mod_tree()

    def _import_enabled_action(self) -> None:
        """导入当前启用的 Mod，并按设置的冲突策略处理目标目录。"""
        self._settings = self._collect_settings_from_form()
        self._persist_state()
        game_mods_root = resolve_game_mods_root(self._settings)
        if game_mods_root is None:
            messagebox.showwarning("Mods 路径未设置", "请先在设置页设置游戏目录或游戏 Mods 目录。")
            return

        enabled_mods = self._enabled_records()
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

    def _check_translation_action(self) -> None:
        """检查当前勾选 Mod 的汉化完整度。"""
        target_mods = self._records_for_batch_action()
        if not target_mods:
            messagebox.showinfo("没有可检查的 Mod", "请先勾选一个 Mod。")
            return
        self._start_worker("检查汉化情况中...", lambda: self._check_translation_worker(target_mods))

    def _request_nexus_api_key_action(self) -> None:
        """启动 Nexus SSO 获取 API Key 的流程。"""
        self._start_worker("获取 Nexus API Key 中...", self._request_nexus_api_key_worker)

    def _nexus_update_targets(self) -> list[ManagedMod]:
        """优先返回勾选项；如果没有勾选，则回退到当前选中项。"""
        checked = self._checked_records()
        if checked:
            return checked
        selected = self._selected_record()
        return [selected] if selected is not None else []

    def _check_nexus_updates_action(self) -> None:
        """启动 Nexus 更新检查。"""
        self._settings = self._collect_settings_from_form()
        if not self._settings.nexus_api_key.strip():
            answer = messagebox.askyesno("Nexus API Key 未设置", "请先在设置页填写 Nexus API Key。现在打开设置吗？")
            if answer:
                self._open_settings_tab()
            return

        target_mods = self._checked_records() or list(self._mods_by_path.values())
        if not target_mods:
            messagebox.showinfo("没有可检查的 Mod", "当前没有可检查的 Mod。")
            return

        self._persist_state()
        self._start_worker("检查 Nexus 更新中...", lambda: self._check_nexus_updates_worker(target_mods, self._settings.nexus_api_key))

    def _download_nexus_updates_action(self) -> None:
        """下载并安装已检查出的 Nexus 更新。"""
        self._settings = self._collect_settings_from_form()
        if not self._settings.nexus_api_key.strip():
            answer = messagebox.askyesno("Nexus API Key 未设置", "请先在设置页填写 Nexus API Key。现在打开设置吗？")
            if answer:
                self._open_settings_tab()
            return

        target_mods = self._nexus_update_targets() or [record for record in self._mods_by_path.values() if record.nexus_update_status == "outdated"]
        if not target_mods:
            messagebox.showinfo("没有可更新的 Mod", "请先勾选或选中一个 Mod。")
            return

        outdated = [record for record in target_mods if record.nexus_update_status == "outdated" or record.nexus_download_url]
        if not outdated:
            messagebox.showinfo("没有可下载的更新", "请先检查 Nexus 更新，确认存在可下载的 Mod。")
            return

        self._persist_state()
        self._start_worker(
            "下载并安装 Nexus 更新中...",
            lambda: self._download_nexus_updates_worker(outdated, self._settings.nexus_api_key),
        )

    def _test_openai_action(self) -> None:
        """使用当前表单值做一次最小化 AI 连通性测试。"""
        self._settings = self._collect_settings_from_form()
        if not self._settings.ai_enabled:
            messagebox.showwarning("AI 未启用", "请先在设置页启用 AI。")
            return
        self._start_worker("测试 AI 配置中...", lambda: self._test_openai_worker(self._settings))

    def _import_worker(self, enabled_mods: list[ManagedMod], game_mods_root: Path, policy: str) -> None:
        """在线程中执行 Mod 复制，并持续上报进度和结果。"""
        try:
            self._queue.put(WorkerEvent(kind="log", message=f"开始导入 {len(enabled_mods)} 个 Mod。"))
            report = deploy_enabled_mods(
                enabled_mods,
                game_mods_root,
                policy=policy,
                progress_callback=lambda index, total, record, phase: self._queue.put(
                    WorkerEvent(
                        kind="progress",
                        progress=index,
                        total=total,
                        message=f"{record.display_name or record.source_path.name}：{phase}",
                    )
                ),
            )
            summary = f"导入完成：复制 {len(report.copied)}，跳过 {len(report.skipped)}，失败 {len(report.failed)}。"
            self._queue.put(WorkerEvent(kind="summary", summary=summary, message=summary))
            for path in report.copied:
                self._queue.put(WorkerEvent(kind="log", message=f"已复制：{path}"))
            for path in report.skipped:
                self._queue.put(WorkerEvent(kind="log", message=f"已跳过：{path}"))
            for path, error in report.failed:
                self._queue.put(WorkerEvent(kind="log", message=f"复制失败：{path} -> {error}"))
        except Exception as exc:
            self._queue.put(WorkerEvent(kind="error", message=f"Import failed: {exc}"))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _check_translation_worker(self, records: list[ManagedMod]) -> None:
        """逐个扫描勾选的 Mod，并汇总汉化完整度、缺失键和异常信息。"""
        total = len(records)
        translated = 0
        partial = 0
        not_translated = 0
        unknown = 0

        ordered = sorted(records, key=lambda item: item.display_name.lower())
        for index, record in enumerate(ordered, start=1):
            display_name = record.display_name or record.source_path.name
            try:
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：检查中"))
                analysis = scan_mod(record.source_path)
                self._apply_analysis_to_record(record, analysis)

                if analysis.translation_status == "translated":
                    translated += 1
                elif analysis.translation_status == "partial":
                    partial += 1
                elif analysis.translation_status == "not_translated":
                    not_translated += 1
                else:
                    unknown += 1

                self._queue.put(
                    WorkerEvent(
                        kind="progress",
                        progress=index,
                        total=total,
                        message=f"[{index}/{total}] {display_name}：{analysis.translation_status}",
                    )
                )
                self._persist_state()
            except Exception as exc:
                unknown += 1
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：检查失败 -> {exc}"))
                self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"已完成 {index}/{total}"))

        summary = f"汉化检查完成：已汉化 {translated}，部分汉化 {partial}，未汉化 {not_translated}，无法判断 {unknown}。"
        self._queue.put(WorkerEvent(kind="summary", summary=summary, message=summary))
        self._queue.put(WorkerEvent(kind="log", message=summary))
        self._queue.put(WorkerEvent(kind="done"))

    def _translate_enabled_action(self) -> None:
        """根据当前勾选的 Mod 和设置，启动 AI 汉化批处理。"""
        self._settings = self._collect_settings_from_form()
        if not self._settings.ai_enabled or not self._settings.translation_enabled:
            messagebox.showwarning("AI 未启用", "请先在设置页启用 AI 和汉化功能。")
            return

        target_mods = self._records_for_batch_action()
        if not target_mods:
            messagebox.showinfo("没有勾选项", "请先勾选至少一个 Mod。")
            return

        dialog = AIOptionsDialog(
            self.root,
            len(target_mods),
            self._settings.openai_model,
            bool(self._settings.openai_api_key.strip()),
        )
        mode = dialog.show()
        if mode is None:
            return

        self._persist_state()
        self._start_worker("汉化处理中...", lambda: self._translate_worker(target_mods, mode))

    def _translate_worker(self, records: list[ManagedMod], mode: str) -> None:
        """逐个处理勾选的 Mod，并在后台执行正式汉化。"""
        total = len(records)
        success = 0
        skipped = 0
        failed = 0
        ordered = sorted(records, key=lambda item: item.display_name.lower())

        for index, record in enumerate(ordered, start=1):
            display_name = record.display_name or record.source_path.name
            try:
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：扫描中"))
                analysis = record.analysis or scan_mod(record.source_path)
                self._apply_analysis_to_record(record, analysis)

                if mode != "force" and analysis.has_chinese and analysis.translation_status == "translated":
                    skipped += 1
                    self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：已汉化，跳过"))
                else:
                    self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：调用 AI 生成中"))
                    result = translate_with_openai(
                        analysis,
                        api_key=self._settings.openai_api_key or None,
                        model=self._settings.openai_model or None,
                        base_url=self._settings.openai_base_url or None,
                    )
                    written = write_json_file(result.output_path, result.payload, source_payload=self._build_validation_source(analysis))
                    refreshed = scan_mod(record.source_path)
                    self._apply_analysis_to_record(record, refreshed)
                    self._persist_state()
                    success += 1
                    self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：已写入 {written}"))
            except Exception as exc:
                failed += 1
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：失败 -> {exc}"))
            finally:
                self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"已完成 {index}/{total}"))

        summary = f"汉化完成：成功 {success}，跳过 {skipped}，失败 {failed}。"
        self._queue.put(WorkerEvent(kind="summary", summary=summary, message=summary))
        self._queue.put(WorkerEvent(kind="log", message=summary))

    def _build_validation_source(self, analysis: ModAnalysis):
        """把扫描结果里的默认语言数据重新组装成写入前的校验源。"""
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
        """读取 JSON 文件，用于重新组装校验源。"""
        import json

        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _apply_analysis_to_record(self, record: ManagedMod, analysis: ModAnalysis) -> None:
        """把最新扫描结果写回 Mod 记录，保持 UI 展示一致。"""
        record.display_name = analysis.mod_name
        record.author = analysis.manifest.author if analysis.manifest is not None else None
        record.version = analysis.manifest.version if analysis.manifest is not None else None
        record.unique_id = analysis.manifest.unique_id if analysis.manifest is not None else None
        record.mod_type = analysis.mod_type
        record.translation_status = analysis.translation_status
        record.has_chinese = analysis.has_chinese
        record.missing_keys_count = analysis.missing_keys_count
        record.has_manifest = analysis.has_manifest
        record.manifest_path = analysis.manifest_path
        record.last_scanned = datetime.now().isoformat(timespec="seconds")
        record.warnings = list(analysis.warnings)
        record.analysis = analysis

    def _refresh_mod_tree(self) -> None:
        """按照当前筛选和排序条件刷新 Mod 列表。"""
        selected = self._mods_tree.selection()[0] if self._mods_tree.selection() else None
        self._mods_tree.delete(*self._mods_tree.get_children())

        records = [record for record in self._mods_by_path.values() if self._matches_filter(record)]
        records.sort(key=self._sort_key, reverse=self._sort_reverse)

        for record in records:
            iid = str(record.source_path.resolve())
            self._mods_tree.insert(
                "",
                END,
                iid=iid,
                values=(
                    _checkbox_symbol(record.checked),
                    _localized_enabled_state(record.enabled),
                    record.display_name or record.source_path.name,
                    _localized_mod_type(record.mod_type),
                    record.version or "",
                    record.nexus_latest_version or "",
                    _localized_nexus_status(record.nexus_update_status),
                    record.author or "",
                    _localized_translation_status(record.translation_status),
                    str(record.source_path),
                ),
            )

        if selected and self._mods_tree.exists(selected):
            self._mods_tree.selection_set(selected)
            self._mods_tree.see(selected)
            self._show_record_details(self._mods_by_path.get(selected))
        else:
            self._clear_record_details()

        self._update_library_summary()
        self._update_selection_summary()
        self._sync_button_states()

    def _matches_filter(self, record: ManagedMod) -> bool:
        """判断某个 Mod 是否匹配当前搜索关键字。"""
        query = self._search_var.get().strip().lower()
        if not query:
            return True
        haystack = " ".join(
            [
                record.display_name,
                record.author or "",
                record.version or "",
                record.unique_id or "",
                record.mod_type,
                _localized_mod_type(record.mod_type),
                record.translation_status,
                _localized_translation_status(record.translation_status),
                record.nexus_update_status,
                _localized_nexus_status(record.nexus_update_status),
                record.nexus_latest_version or "",
                _boolean_label(record.enabled),
                _boolean_label(record.checked),
                _boolean_label(record.has_chinese),
                str(record.source_path),
                " ".join(record.tags),
                record.notes,
            ]
        ).lower()
        return query in haystack

    def _sort_key(self, record: ManagedMod):
        """返回当前列对应的排序键。"""
        columns = {
            "checked": (0 if record.checked else 1, record.display_name.lower()),
            "enabled": (0 if record.enabled else 1, record.display_name.lower()),
            "display_name": record.display_name.lower(),
            "mod_type": record.mod_type,
            "version": record.version or "",
            "nexus_latest_version": record.nexus_latest_version or "",
            "nexus_update_status": record.nexus_update_status,
            "author": record.author or "",
            "translation_status": record.translation_status,
            "path": str(record.source_path).lower(),
        }
        return columns.get(self._sort_column, record.display_name.lower())

    def _toggle_sort(self, column: str) -> None:
        """切换列表排序列或排序方向。"""
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self._refresh_mod_tree()

    def _select_all_mods(self) -> None:
        """勾选列表中的所有 Mod。"""
        for iid in self._mods_tree.get_children():
            record = self._mods_by_path.get(iid)
            if record is None:
                continue
            record.checked = True
        self._persist_state()
        self._refresh_mod_tree()
        self._update_selection_summary()

    def _clear_selection(self) -> None:
        """清空当前勾选。"""
        for iid in self._mods_tree.get_children():
            record = self._mods_by_path.get(iid)
            if record is None:
                continue
            record.checked = False
        self._persist_state()
        self._refresh_mod_tree()
        self._update_selection_summary()

    def _invert_selection(self) -> None:
        """反转当前勾选状态。"""
        for iid in self._mods_tree.get_children():
            record = self._mods_by_path.get(iid)
            if record is None:
                continue
            record.checked = not record.checked
        self._persist_state()
        self._refresh_mod_tree()
        self._update_selection_summary()

    def _on_tree_click(self, event) -> str | None:
        """点击勾选列或启用列时直接切换对应状态。"""
        if self._mods_tree.identify_region(event.x, event.y) != "cell":
            return None
        column = self._mods_tree.identify_column(event.x)
        iid = self._mods_tree.identify_row(event.y)
        if not iid:
            return None

        record = self._mods_by_path.get(iid)
        if record is None:
            return None

        if column == "#1":
            record.checked = not record.checked
        elif column == "#2":
            record.enabled = not record.enabled
        else:
            return None
        self._persist_state()
        self._refresh_mod_tree()
        self._mods_tree.selection_set(iid)
        return "break"

    def _on_tree_select(self, _event: object) -> None:
        """在列表选择变化时同步详情面板内容。"""
        selection = self._mods_tree.selection()
        self._update_selection_summary()
        self._sync_button_states()
        if not selection:
            self._current_selected_path = None
            self._clear_record_details()
            return
        key = selection[0]
        self._current_selected_path = key
        self._show_record_details(self._mods_by_path.get(key))

    def _selected_record(self) -> ManagedMod | None:
        """返回当前选中的单个 Mod 记录。"""
        selection = self._mods_tree.selection()
        if not selection:
            return None
        return self._mods_by_path.get(selection[0])

    def _selected_records(self) -> list[ManagedMod]:
        """返回当前所有选中的 Mod 记录。"""
        return [self._mods_by_path[iid] for iid in self._mods_tree.selection() if iid in self._mods_by_path]

    def _update_selection_summary(self) -> None:
        """更新当前选中与勾选数量，并同步库摘要。"""
        selected_count = len(self._mods_tree.selection())
        checked_count = len(self._checked_records())
        self._selected_count_var.set(f"已选中 {selected_count} 个 | 已勾选 {checked_count} 个")
        self._update_library_summary()

    def _checked_records(self) -> list[ManagedMod]:
        """返回所有已勾选的 Mod 记录。"""
        return [record for record in self._mods_by_path.values() if record.checked]

    def _enabled_records(self) -> list[ManagedMod]:
        """返回所有已启用的 Mod。"""
        return [record for record in self._mods_by_path.values() if record.enabled]

    def _records_for_batch_action(self) -> list[ManagedMod]:
        """优先使用当前勾选项。"""
        return self._checked_records()

    def _set_selected_enabled(self, enabled: bool) -> None:
        """批量设置当前勾选 Mod 的启用状态。"""
        records = self._checked_records()
        if not records:
            messagebox.showinfo("未勾选 Mod", "请先勾选一个 Mod。")
            return
        for record in records:
            record.enabled = enabled
        self._persist_state()
        self._refresh_mod_tree()

    def _save_selected_metadata(self) -> None:
        """保存当前选中 Mod 的标签和备注。"""
        record = self._selected_record()
        if record is None:
            messagebox.showinfo("未选择 Mod", "请先选择一个 Mod。")
            return

        tags = [item.strip() for item in self._tags_var.get().replace("；", ";").split(";") if item.strip()]
        if len(tags) == 1 and "," in tags[0]:
            tags = [item.strip() for item in tags[0].split(",") if item.strip()]
        record.tags = tags
        record.notes = self._notes_text.get("1.0", END).strip()
        self._persist_state()
        self._refresh_mod_tree()
        self._append_log(f"已保存元数据：{record.display_name or record.source_path.name}")

    def _show_record_details(self, record: ManagedMod | None) -> None:
        """把某个 Mod 的详情写入右侧面板。"""
        if record is None:
            self._clear_record_details()
            return
        self._tags_var.set(", ".join(record.tags))
        self._notes_text.configure(state="normal")
        self._notes_text.delete("1.0", END)
        self._notes_text.insert("1.0", record.notes)
        self._notes_text.configure(state="normal")
        self._set_text(self._detail_text, self._render_record_summary(record))

    def _clear_record_details(self) -> None:
        """清空详情面板并恢复默认提示。"""
        self._tags_var.set("")
        self._notes_text.configure(state="normal")
        self._notes_text.delete("1.0", END)
        self._notes_text.configure(state="normal")
        self._set_text(self._detail_text, "选择一个 Mod 查看详情。")

    def _render_record_summary(self, record: ManagedMod) -> str:
        """把 Mod 记录格式化成可读的详情文本。"""
        lines = [
            f"路径：{record.source_path}",
            f"已勾选：{_boolean_label(record.checked)}",
            f"已启用：{_boolean_label(record.enabled)}",
            f"名称：{record.display_name or record.source_path.name}",
            f"作者：{record.author or '无'}",
            f"版本：{record.version or '无'}",
            f"类型：{_localized_mod_type(record.mod_type)}",
            f"唯一 ID：{record.unique_id or '无'}",
            f"汉化状态：{_localized_translation_status(record.translation_status)}",
            f"是否有中文：{_boolean_label(record.has_chinese)}",
            f"缺失键数：{record.missing_keys_count}",
            f"Nexus 更新状态：{_localized_nexus_status(record.nexus_update_status)}",
            f"Nexus 当前版本：{record.nexus_current_version or '无'}",
            f"Nexus 远端版本：{record.nexus_latest_version or '无'}",
            f"Nexus 文件名：{record.nexus_file_name or '无'}",
            f"Nexus 最后检查：{record.nexus_last_checked or '无'}",
            f"Nexus 提示：{record.nexus_message or '无'}",
            f"清单：{'已找到' if record.has_manifest else '缺失'}",
            f"清单路径：{record.manifest_path or '无'}",
            f"标签：{', '.join(record.tags) or '无'}",
            f"备注：{record.notes or '无'}",
        ]
        if record.warnings:
            lines.append("警告：")
            lines.extend(f"- {warning}" for warning in record.warnings)
        return "\n".join(lines)

    def _update_library_summary(self) -> None:
        """更新左上角的库统计摘要。"""
        total = len(self._mods_by_path)
        enabled = sum(1 for record in self._mods_by_path.values() if record.enabled)
        summary = [f"总数：{total}", f"已启用：{enabled}", f"已选择：{len(self._mods_tree.selection())}"]
        if self._settings.library_root is not None:
            summary.append(f"库目录：{self._settings.library_root}")
        self._library_summary_var.set(" | ".join(summary))

    def _translate_enabled_action(self) -> None:
        """根据当前选择的 Mod 和设置，启动 AI 汉化批处理。"""
        self._settings = self._collect_settings_from_form()
        if not self._settings.ai_enabled or not self._settings.translation_enabled:
            messagebox.showwarning("AI 未启用", "请先在设置页启用 AI 和汉化功能。")
            return

        enabled_mods = self._checked_records()
        if not enabled_mods:
            messagebox.showinfo("没有勾选项", "请先勾选至少一个 Mod。")
            return

        dialog = AIOptionsDialog(
            self.root,
            len(enabled_mods),
            self._settings.openai_model,
            bool(self._settings.openai_api_key.strip()),
        )
        mode = dialog.show()
        if mode is None:
            return

        self._persist_state()
        self._start_worker("汉化处理中...", lambda: self._translate_worker(enabled_mods, mode))

    def _test_openai_worker(self, settings: AppSettings) -> None:
        """在线程中执行 OpenAI 连通性测试并把结果推回 UI。"""
        try:
            self._queue.put(WorkerEvent(kind="log", message="开始测试 AI 配置。"))
            reply = probe_openai_connection(
                api_key=settings.openai_api_key or None,
                model=settings.openai_model or None,
                base_url=settings.openai_base_url or None,
            )
            message = f"AI 配置可用：{reply}"
            self._queue.put(WorkerEvent(kind="ai_test_success", message=message, summary=message))
        except Exception as exc:
            message = f"AI 配置测试失败：{exc}"
            self._queue.put(WorkerEvent(kind="ai_test_failure", message=message, summary=message))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _request_nexus_api_key_worker(self) -> None:
        """在线程中执行 Nexus SSO 登录并获取 API Key。"""
        session = NexusAuthSession()
        try:
            self._queue.put(WorkerEvent(kind="log", message="正在打开 Nexus SSO 页面并等待返回 API Key。"))
            result = session.acquire_api_key()
            if result.api_key:
                self._queue.put(WorkerEvent(kind="nexus_key_success", message=result.message, api_key=result.api_key, summary=result.message))
            else:
                error_message = result.error or result.message or "无法获取 Nexus API Key。"
                self._queue.put(WorkerEvent(kind="nexus_key_failure", message=error_message, summary=error_message))
        except Exception as exc:
            error_message = f"获取 Nexus API Key 失败：{exc}"
            self._queue.put(WorkerEvent(kind="nexus_key_failure", message=error_message, summary=error_message))
        finally:
            self._queue.put(WorkerEvent(kind="done"))

    def _apply_nexus_update_to_record(self, record: ManagedMod, info) -> None:
        """把 Nexus 更新结果写回 Mod 记录。"""
        record.nexus_mod_id = info.mod_id
        record.nexus_file_id = info.file_id
        record.nexus_update_status = info.status
        record.nexus_current_version = info.current_version
        record.nexus_latest_version = info.latest_version
        record.nexus_file_name = info.file_name
        record.nexus_update_url = info.update_url
        record.nexus_download_url = info.download_url
        record.nexus_last_checked = info.checked_at
        record.nexus_message = info.message

    def _check_nexus_updates_worker(self, records: list[ManagedMod], api_key: str) -> None:
        """在线程中检查 Nexus 更新，并把结果回写到本地记录。"""
        service = NexusService(api_key)
        total = len(records)
        outdated = 0
        up_to_date = 0
        no_source = 0
        unknown = 0
        failed = 0

        for index, record in enumerate(sorted(records, key=lambda item: item.display_name.lower()), start=1):
            display_name = record.display_name or record.source_path.name
            try:
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：检查 Nexus 更新中"))
                info = service.check_mod(record)
                self._apply_nexus_update_to_record(record, info)
                self._persist_state()

                if info.status == "outdated":
                    outdated += 1
                elif info.status == "up_to_date":
                    up_to_date += 1
                elif info.status == "no_source":
                    no_source += 1
                else:
                    unknown += 1

                self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"[{index}/{total}] {display_name}：{_localized_nexus_status(info.status)}"))
            except Exception as exc:
                failed += 1
                record.nexus_update_status = "failed"
                record.nexus_message = str(exc)
                record.nexus_last_checked = datetime.now().isoformat(timespec="seconds")
                self._persist_state()
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：检查失败 -> {exc}"))
                self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"[{index}/{total}] {display_name}：失败"))

        summary = f"Nexus 更新检查完成：可更新 {outdated}，已最新 {up_to_date}，无来源 {no_source}，未知 {unknown}，失败 {failed}。"
        self._queue.put(WorkerEvent(kind="summary", summary=summary, message=summary))
        self._queue.put(WorkerEvent(kind="log", message=summary))
        self._queue.put(WorkerEvent(kind="done"))

    def _download_nexus_updates_worker(self, records: list[ManagedMod], api_key: str) -> None:
        """在线程中下载并安装 Nexus 更新。"""
        service = NexusService(api_key)
        total = len(records)
        installed = 0
        skipped = 0
        failed = 0

        for index, record in enumerate(sorted(records, key=lambda item: item.display_name.lower()), start=1):
            display_name = record.display_name or record.source_path.name
            try:
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：准备下载更新"))
                info = service.check_mod(record) if record.nexus_update_status not in {"outdated", "up_to_date"} else None
                if info is not None:
                    self._apply_nexus_update_to_record(record, info)

                effective_info = info or service.check_mod(record)
                if effective_info.status != "outdated" or not effective_info.download_url:
                    skipped += 1
                    record.nexus_message = effective_info.message or "没有可下载的更新。"
                    self._persist_state()
                    self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：跳过（{record.nexus_message}）"))
                    self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"[{index}/{total}] {display_name}：跳过"))
                    continue

                archive_path = service.download_update(effective_info)
                try:
                    install_result = service.install_download(record, archive_path)
                    refreshed = scan_mod(record.source_path)
                    self._apply_analysis_to_record(record, refreshed)
                    record.nexus_update_status = "up_to_date"
                    record.nexus_current_version = refreshed.manifest.version if refreshed.manifest is not None else effective_info.latest_version
                    record.nexus_latest_version = effective_info.latest_version
                    record.nexus_file_name = effective_info.file_name
                    record.nexus_update_url = effective_info.update_url
                    record.nexus_download_url = effective_info.download_url
                    record.nexus_last_checked = datetime.now().isoformat(timespec="seconds")
                    record.nexus_message = install_result.message
                    self._persist_state()
                    installed += 1
                    self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：{install_result.message}"))
                    self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"[{index}/{total}] {display_name}：已安装"))
                finally:
                    shutil.rmtree(archive_path.parent, ignore_errors=True)
            except Exception as exc:
                failed += 1
                record.nexus_update_status = "failed"
                record.nexus_message = str(exc)
                record.nexus_last_checked = datetime.now().isoformat(timespec="seconds")
                self._persist_state()
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：安装失败 -> {exc}"))
                self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"[{index}/{total}] {display_name}：失败"))

        summary = f"Nexus 更新处理完成：已安装 {installed}，跳过 {skipped}，失败 {failed}。"
        self._queue.put(WorkerEvent(kind="summary", summary=summary, message=summary))
        self._queue.put(WorkerEvent(kind="log", message=summary))
        self._queue.put(WorkerEvent(kind="done"))

    def _translate_worker(self, records: list[ManagedMod], mode: str) -> None:
        """逐个处理选中的 Mod，并在后台执行正式汉化。"""
        total = len(records)
        success = 0
        skipped = 0
        failed = 0

        ordered = sorted(records, key=lambda item: item.display_name.lower())
        for index, record in enumerate(ordered, start=1):
            display_name = record.display_name or record.source_path.name
            try:
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：扫描中"))
                analysis = record.analysis or scan_mod(record.source_path)
                self._apply_analysis_to_record(record, analysis)

                if mode != "force" and analysis.has_chinese and analysis.translation_status == "translated":
                    skipped += 1
                    self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：已汉化，跳过"))
                    continue

                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：调用 AI 生成中"))
                result = translate_with_openai(
                    analysis,
                    api_key=self._settings.openai_api_key or None,
                    model=self._settings.openai_model or None,
                    base_url=self._settings.openai_base_url or None,
                )
                written = write_json_file(result.output_path, result.payload, source_payload=self._build_validation_source(analysis))
                refreshed = scan_mod(record.source_path)
                self._apply_analysis_to_record(record, refreshed)
                self._persist_state()
                success += 1
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：已写入 {written}"))
                self._queue.put(WorkerEvent(kind="progress", progress=index, total=total, message=f"已完成 {index}/{total}"))
            except Exception as exc:
                failed += 1
                self._queue.put(WorkerEvent(kind="log", message=f"[{index}/{total}] {display_name}：失败 -> {exc}"))

        summary = f"汉化完成：成功 {success}，跳过 {skipped}，失败 {failed}。"
        self._queue.put(WorkerEvent(kind="summary", summary=summary, message=summary))
        self._queue.put(WorkerEvent(kind="log", message=summary))

    def _build_validation_source(self, analysis: ModAnalysis):
        """把扫描结果里的默认语言数据重新组装成写入前的校验源。"""
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
        """读取 JSON 文件，用于重新组装校验源。"""
        import json

        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _apply_analysis_to_record(self, record: ManagedMod, analysis: ModAnalysis) -> None:
        """把最新扫描结果写回 Mod 记录，保持 UI 展示一致。"""
        record.display_name = analysis.mod_name
        record.author = analysis.manifest.author if analysis.manifest is not None else None
        record.version = analysis.manifest.version if analysis.manifest is not None else None
        record.unique_id = analysis.manifest.unique_id if analysis.manifest is not None else None
        record.mod_type = analysis.mod_type
        record.translation_status = analysis.translation_status
        record.has_chinese = analysis.has_chinese
        record.missing_keys_count = analysis.missing_keys_count
        record.has_manifest = analysis.has_manifest
        record.manifest_path = analysis.manifest_path
        record.last_scanned = datetime.now().isoformat(timespec="seconds")
        record.warnings = list(analysis.warnings)
        record.analysis = analysis

    def _sync_button_states(self) -> None:
        """根据当前选择、配置和后台运行状态切换按钮可用性。"""
        has_selection = self._selected_record() is not None
        has_library = self._parse_path(self._library_root_var.get()) is not None
        has_checked = bool(self._checked_records())
        enabled_records = self._enabled_records()
        has_enabled = bool(enabled_records)
        ai_ready = self._ai_enabled_var.get() and self._translation_enabled_var.get() and has_enabled
        ai_enabled = self._ai_enabled_var.get()

        action_state = "disabled" if self._worker_running else "normal"
        selected_state = "disabled" if self._worker_running or not has_selection else "normal"

        self._scan_button.configure(state=action_state if has_library else "disabled")
        self._import_enabled_button.configure(state=action_state if has_enabled else "disabled")
        self._check_translation_button.configure(state=action_state if has_checked else "disabled")
        self._check_nexus_updates_button.configure(state=action_state if has_library else "disabled")
        self._download_nexus_updates_button.configure(state=action_state if has_library else "disabled")
        self._nexus_api_key_button.configure(state=action_state)
        self._translate_button.configure(state=action_state if (ai_ready and has_checked) else "disabled")
        has_visible_records = bool(self._mods_tree.get_children())
        self._select_all_button.configure(state=action_state if has_visible_records else "disabled")
        self._clear_selection_button.configure(state=action_state if has_visible_records else "disabled")
        self._invert_selection_button.configure(state=action_state if has_visible_records else "disabled")
        self._enable_button.configure(state=selected_state)
        self._disable_button.configure(state=selected_state)
        self._save_meta_button.configure(state=selected_state)
        self._save_settings_button.configure(state=action_state)
        self._test_ai_button.configure(state=action_state if ai_enabled else "disabled")

    def _start_worker(self, status: str, action: Callable[[], None]) -> None:
        """统一启动后台线程，并先把界面切到忙碌状态。"""
        if self._worker_running:
            self._append_log("已有任务正在运行。")
            return
        self._worker_running = True
        self._status_var.set(status)
        self._sync_button_states()
        threading.Thread(target=action, daemon=True).start()

    def _poll_queue(self) -> None:
        """轮询后台队列，把事件转成 UI 更新。"""
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
        """处理后台线程发回的单条事件。"""
        if event.kind == "log":
            self._append_log(event.message)
            self._status_var.set(event.message)
        elif event.kind == "progress":
            total = event.total or 0
            progress = event.progress or 0
            if total > 0:
                percent = int((progress / total) * 100)
                self._progress_bar.configure(maximum=total)
                self._progress_bar["value"] = progress
                self._progress_var.set(f"{percent}%")
            if event.message:
                self._status_var.set(event.message)
        elif event.kind == "summary":
            if event.summary:
                self._summary_var.set(event.summary)
                self._append_log(event.summary)
        elif event.kind == "ai_test_success":
            self._append_log(event.message)
            self._status_var.set(event.message)
            self._summary_var.set(event.summary or event.message)
            messagebox.showinfo("AI 测试成功", event.message)
        elif event.kind == "ai_test_failure":
            self._append_log(event.message)
            self._status_var.set(event.message)
            self._summary_var.set(event.summary or event.message)
            messagebox.showerror("AI 测试失败", event.message)
        elif event.kind == "nexus_key_success":
            if event.api_key:
                self._nexus_api_key_var.set(event.api_key)
            self._persist_state()
            self._append_log(event.message)
            self._status_var.set(event.message)
            self._summary_var.set(event.summary or event.message)
            messagebox.showinfo("Nexus API Key 获取成功", event.message)
        elif event.kind == "nexus_key_failure":
            self._append_log(event.message)
            self._status_var.set(event.message)
            self._summary_var.set(event.summary or event.message)
            messagebox.showerror("Nexus API Key 获取失败", event.message)
        elif event.kind == "library_scan":
            self._refresh_library_from_event(event.mods)
            self._append_log(event.message)
            self._status_var.set(event.message)
        elif event.kind == "error":
            self._append_log(event.message)
            self._status_var.set(event.message)
        elif event.kind == "done":
            self._worker_running = False
            self._sync_button_states()
            self._progress_bar["value"] = 0
            self._progress_var.set("0%")
            self._refresh_mod_tree()
            if self._status_var.get() in {"扫描 Mod 库中...", "汉化处理中...", "导入启用的 Mod 中...", "检查 Nexus 更新中...", "下载并安装 Nexus 更新中...", "检查汉化情况中...", "测试 AI 配置中..."}:
                self._status_var.set("Idle")

    def _append_log(self, message: str) -> None:
        """把一条消息追加到日志窗口。"""
        timestamp = time.strftime("%H:%M:%S")
        self._log_text.configure(state="normal")
        self._log_text.insert(END, f"[{timestamp}] {message}\n")
        self._log_text.see(END)
        self._log_text.configure(state="disabled")

    def _set_text(self, widget: Text, value: str) -> None:
        """把只读文本控件替换为新的内容。"""
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")


TranslationApp = ModManagerApp
