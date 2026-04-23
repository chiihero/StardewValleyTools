# Stardew Valley Mod Manager

Python 3.11+ tkinter utility for managing a Stardew Valley mod library, importing enabled mods into the game `Mods` folder, and generating safe Chinese locale files with AI.

## Run

```bash
python app.py
```

## Workflow

1. Set the mod library path and game path in Settings.
2. Scan the library and toggle enabled / disabled directly from the Mod list.
3. Use the bottom action buttons to import enabled mods or run batch AI translation.
4. AI translation writes a safe `i18n/zh.generated.json` file and logs progress.

## Notes

- Enabled / disabled is stored as manager metadata only.
- Import only copies enabled mods; it does not delete other mods in the game folder.
- AI translation defaults to OpenAI and uses the saved API settings when you click generate.
