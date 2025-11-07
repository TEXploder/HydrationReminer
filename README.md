# Hydration Reminder Overlay

Elegant Windows overlay that keeps you hydrated by periodically surfacing a gorgeous, animated notification above every other window (borderless-windowed friendly). It ships with a tray icon, persistent settings, multi-monitor support, and configurable entry animations so the reminder feels at home on your desktop.

## Highlights

- **Always-on-top WinAPI overlay** rendered via Qt/PySide6 with layered transparency and soft shadows.
- **Animated artwork**: drop `frame1.png`, `frame2.png`, … into `assets/` (or use `bottle.gif`) and the overlay scales/animates them automatically.
- **Smart scheduling**: reminder interval, random delay, auto-hide timeout, animation cadence, and per-monitor placement are all user-configurable.
- **Live settings dialog**: opens automatically on first launch, features a live preview, wheel-safe inputs, and instant persistence in `C:\TEX-Programme\HydrationReminder\settings.json`.
- **Entry animations**: choose how the overlay appears (`fade`, `slide`, or `pop`) independent of the GIF frames.
- **System tray**: quick “show now” command, remaining-time readout, persistent autostart toggle (HKCU Run), and a direct link to the settings window.

## Requirements

- Windows 10/11
- Python 3.9+ (3.11 recommended)
- [PySide6](https://pypi.org/project/PySide6/) (LGPL)

Install dependencies:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Running

```powershell
python drink_reminder.py
```

On first launch the settings dialog pops up automatically so you can pick the monitor, timings, animation style, colors, etc. Afterwards the app minimizes to the system tray; use the tray icon to re-open settings or trigger an instant reminder.

### Persistent storage & assets

- **Settings + user assets** live in `C:\TEX-Programme\HydrationReminder\` (or `%USERPROFILE%\HydrationReminder\` if the main path is unavailable).  
- The directory is auto-created on startup and default assets from `./assets` are copied there if missing.
- Drop your PNG sequence (`frame1.png`, `frame2.png`, … up to `frame24.png`) or a fallback `animation.gif` into that folder—changes are picked up automatically on the next reminder.

## CLI overrides

Every option in the UI also has a command-line override (handy for scripting or testing):

| Option | Description |
| --- | --- |
| `--interval MINUTES` | Reminder interval (default 45). |
| `--autohide SECONDS` | Auto-hide timeout (default 15). |
| `--animation-speed SECONDS` | Frame cadence for PNG animation (default 0.2). |
| `--random SECONDS` | Adds a random delay (0…N) to each reminder. |
| `--position` | `bottom_right`, `bottom_left`, `top_right`, `top_left`. |
| `--margin-x`, `--margin-y` | Pixel offsets from screen edges. |
| `--width`, `--height` | Minimum overlay size (auto-expands if needed). |
| `--opacity` | Overlay opacity (0.1–1.0). |
| `--monitor NAME` | Force a specific monitor (see settings combo for the exact name). |
| `--entry-animation {fade,slide,pop}` | Choose the visual intro effect. |
| `--no-preview` | Suppress the initial quick reminder preview. |

CLI values override whatever is currently stored in `settings.json` and are written back immediately.

## Packaging (one-file EXE)

```powershell
pip install pyinstaller
pyinstaller --onefile --windowed drink_reminder.py
```

- Copy the generated EXE from `dist/`.  
- Ship the **PySide6 LGPL notice** alongside the executable as required by the license.  
- The app still writes its settings/assets to `C:\TEX-Programme\HydrationReminder\`, so your custom art survives updates.

## Project structure

```
.
├── assets/               # default PNG frames copied to the user storage on first launch
├── drink_reminder.py     # main application
├── requirements.txt
├── README.md
└── .gitignore
```

## Tips

- Use the tray tooltip/first menu item to monitor “time until next reminder”.
- The settings dialog blocks accidental mouse-wheel changes, so feel free to scroll even while hovering sliders or spin boxes.
- After editing your PNG sequence, hit “Overlay jetzt anzeigen” inside the settings window for an instant preview.

Happy hydrating! 🥤
