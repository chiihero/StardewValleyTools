# Stardew Valley Mod Manager

Python 3.11+ tkinter utility for managing a Stardew Valley mod library, importing enabled mods into the game `Mods` folder, checking Nexus-based mod updates, and generating safe Chinese locale files with AI.

## Run

```bash
python app.py
```

## Workflow

1. Set the mod library path and game path in Settings.
2. Scan the library, use the "勾选" column for batch selection, and toggle enabled / disabled in the separate "启用" column.
3. Use the bottom action buttons in the Mod list card to check translation status, check Nexus updates, download/install updates, import enabled mods, or run batch AI translation.
4. AI translation writes to `i18n/zh.json` and logs progress.

## Notes

- Enabled / disabled is stored as manager metadata only.
- Nexus update checks rely on the Nexus API Key saved in Settings and on each mod's Nexus update keys in `manifest.json`.
- Nexus update archives are extracted locally; 7z packages require the `py7zr` dependency.
- The Nexus API Key field includes a button that opens the Nexus SSO page and auto-fills the key after authorization.
- 切换 Mod 选择后，相关操作按钮会立即跟随当前选择状态刷新，不需要额外点一次“重新扫描”。
- 勾选列用于批量操作；详情面板仍只跟随单行选中。
- Mod 列表卡片底部的批量操作按钮会保持在同一容器内，窗口较小时也更容易完整显示。
- Import only copies enabled mods; it does not delete other mods in the game folder.
- AI translation defaults to OpenAI, uses `gpt-5.4-nano`, and the `Base URL` field defaults to `https://api.openai.com/v1`.
- You can still change `Base URL` to a proxy endpoint when needed.
- The Settings page includes an AI test button that sends one minimal request with the current API Key / model / Base URL.
