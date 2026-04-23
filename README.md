# Stardew Valley Mod Manager

Python 3.11+ tkinter utility for managing a Stardew Valley mod library, importing enabled mods into the game `Mods` folder, and generating safe Chinese locale files with AI.

## Run

```bash
python app.py
```

## Workflow

1. Set the mod library path and game path in Settings.
2. Scan the library and toggle enabled / disabled directly from the Mod list.
3. Use the bottom action buttons to check translation status, import enabled mods, or run batch AI translation.
4. AI translation writes to `i18n/zh.json` and logs progress.

## Notes

- Enabled / disabled is stored as manager metadata only.
- 切换 Mod 选择后，相关操作按钮会立即跟随当前选择状态刷新，不需要额外点一次“重新扫描”。
- Import only copies enabled mods; it does not delete other mods in the game folder.
- AI translation defaults to OpenAI, uses `gpt-5.4-nano`, and the `Base URL` field defaults to `https://api.openai.com/v1`.
- You can still change `Base URL` to a proxy endpoint when needed.
- The Settings page includes an AI test button that sends one minimal request with the current API Key / model / Base URL.
