# pinight

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![release](https://img.shields.io/github/v/release/eagnespuerto/pinight?include_prereleases&sort=semver)](https://github.com/eagnespuerto/pinight/releases/latest)
[![made with](https://img.shields.io/badge/made%20with-Python-3776AB?logo=python&logoColor=white)](https://github.com/eagnespuerto/pinight)
[![platform](https://img.shields.io/badge/platform-Raspberry%20Pi-A22846?logo=raspberrypi&logoColor=white)](https://github.com/eagnespuerto/pinight#readme)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support%20me-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/eagnespuerto)
[![Linktree](https://img.shields.io/badge/Linktree-soitsagnes-43E55E?logo=linktree&logoColor=white)](https://linktr.ee/soitsagnes)

A tiny GUI to warm your display and dim its brightness on Raspberry Pi OS. Wraps `redshift` (or `gammastep`) with two sliders and an on/off toggle.

Works on 32-bit and 64-bit Pi OS, from **Pi Zero** upward. Stdlib Python + Tkinter only — no PyPI dependencies.

## What it does

- **Color temperature** slider: 2500 K (warm, blue-light filtered) to 6500 K (daylight).
- **Brightness** slider: 0.3 to 1.0 (software gamma scaling — dims without touching hardware backlight).
- **Enable** checkbox: toggles the effect on/off system-wide for the session.
- Settings are saved and re-applied automatically after login.

## Install

```bash
git clone https://github.com/<you>/pinight.git
cd pinight
./install.sh
```

The installer will:

- `apt install` any missing deps (`redshift`, `python3-tk`)
- copy `pinight` to `~/.local/bin`
- add a menu entry under Preferences / Settings
- add an autostart entry so your saved settings re-apply after login (`pinight --apply-saved`)

## Use

Launch from the Applications menu, or run `pinight` in a terminal.

Headless mode (used by autostart, or for scripting):

```bash
pinight --apply-saved
```

## Uninstall

```bash
./uninstall.sh
```

## Requirements

- Raspberry Pi OS (Bullseye or newer). 32-bit or 64-bit.
- X11 session (the default on 32-bit Pi OS and on Pi 4 or earlier). Wayland sessions (Pi 5 on 64-bit Bookworm) are also supported via `gammastep`, which is detected automatically if installed.
- `python3` with `tkinter`.
- `redshift` **or** `gammastep`.

Tested target: Pi Zero W (ARMv6) through Pi 5.

## Notes

- The brightness slider is *software* gamma, not hardware backlight. For real backlight control on the official DSI touchscreen, write to `/sys/class/backlight/*/brightness`.
- Effect is per-session. That's why an autostart entry is included — it re-applies your saved settings without opening the GUI.
- If you only want the effect at night, redshift's own scheduling (`redshift -l LAT:LON`) is more flexible than this GUI. pinight is deliberately manual.

## Why not just use `redshift` directly?

You can. This is a small wrapper for people who want a two-slider GUI without editing redshift's config file, providing geo coordinates, or wiring up a systemd unit.

## License

MIT
