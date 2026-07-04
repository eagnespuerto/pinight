#!/usr/bin/env python3
"""pinight — GUI control for display warmth and brightness on Raspberry Pi OS.

Wraps redshift/gammastep. Stdlib only (Tkinter) so it runs on ARMv6 (Pi Zero)
without needing pip. Works on X11 (default on 32-bit Pi OS) and Wayland.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

APP_NAME = "pinight"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "temperature": 6500,   # K, daylight
    "brightness": 1.0,     # 0.3..1.0, software gamma scaling
    "enabled": False,
}

TEMP_MIN, TEMP_MAX = 2500, 6500
BRIGHT_MIN, BRIGHT_MAX = 0.3, 1.0
DEBOUNCE_MS = 120


def find_backend():
    for name in ("redshift", "gammastep"):
        if shutil.which(name):
            return name
    return None


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (json.JSONDecodeError, OSError):
            pass
    cfg["temperature"] = max(TEMP_MIN, min(TEMP_MAX, int(cfg["temperature"])))
    cfg["brightness"] = max(BRIGHT_MIN, min(BRIGHT_MAX, float(cfg["brightness"])))
    cfg["enabled"] = bool(cfg["enabled"])
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def apply_settings(backend, temperature, brightness):
    subprocess.run(
        [backend, "-P", "-O", str(int(temperature)), "-b", f"{float(brightness):.2f}"],
        check=False, capture_output=True, timeout=5,
    )


def reset_display(backend):
    subprocess.run(
        [backend, "-x"],
        check=False, capture_output=True, timeout=5,
    )


class PinightApp:
    def __init__(self, root, backend):
        self.root = root
        self.backend = backend
        self.config = load_config()
        self._debounce_job = None

        root.title("pinight")
        root.resizable(False, False)

        self._build_ui()

        if self.config["enabled"]:
            apply_settings(backend, self.config["temperature"], self.config["brightness"])

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=16)
        frm.grid(row=0, column=0)

        self.enabled_var = tk.BooleanVar(value=self.config["enabled"])
        ttk.Checkbutton(
            frm, text="Enable warmth and dimming", variable=self.enabled_var,
            command=self._on_toggle,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Separator(frm, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(10, 8)
        )

        ttk.Label(frm, text="Color temperature").grid(row=2, column=0, sticky="w")
        self.temp_var = tk.IntVar(value=self.config["temperature"])
        self.temp_label = ttk.Label(frm, width=8, anchor="e")
        self.temp_label.grid(row=2, column=1, sticky="e")
        ttk.Scale(
            frm, from_=TEMP_MIN, to=TEMP_MAX, orient="horizontal", length=340,
            variable=self.temp_var, command=self._on_temp,
        ).grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            frm, text=f"warm {TEMP_MIN} K   <->   {TEMP_MAX} K daylight",
            foreground="#888",
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        ttk.Label(frm, text="Brightness").grid(row=5, column=0, sticky="w", pady=(14, 0))
        self.bright_var = tk.DoubleVar(value=self.config["brightness"])
        self.bright_label = ttk.Label(frm, width=8, anchor="e")
        self.bright_label.grid(row=5, column=1, sticky="e", pady=(14, 0))
        ttk.Scale(
            frm, from_=BRIGHT_MIN, to=BRIGHT_MAX, orient="horizontal", length=340,
            variable=self.bright_var, command=self._on_bright,
        ).grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            frm, text=f"dim {BRIGHT_MIN:.1f}   <->   {BRIGHT_MAX:.1f} full  (software gamma)",
            foreground="#888",
        ).grid(row=7, column=0, columnspan=2, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Button(btns, text="Reset defaults", command=self._on_reset).pack(side="left")
        ttk.Button(btns, text="Save and close", command=self._on_close).pack(side="right")

        ttk.Label(frm, text=f"backend: {self.backend}", foreground="#888").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

        self._update_labels()

    def _update_labels(self):
        self.temp_label.config(text=f"{int(self.temp_var.get())} K")
        self.bright_label.config(text=f"{float(self.bright_var.get()):.2f}")

    def _on_temp(self, _val):
        self._update_labels()
        self._debounced_apply()

    def _on_bright(self, _val):
        self._update_labels()
        self._debounced_apply()

    def _on_toggle(self):
        if self.enabled_var.get():
            self._apply_now()
        else:
            reset_display(self.backend)
        self._persist()

    def _debounced_apply(self):
        if self._debounce_job is not None:
            self.root.after_cancel(self._debounce_job)
        self._debounce_job = self.root.after(DEBOUNCE_MS, self._apply_now)

    def _apply_now(self):
        self._debounce_job = None
        if self.enabled_var.get():
            apply_settings(
                self.backend, int(self.temp_var.get()), float(self.bright_var.get())
            )
        self._persist()

    def _on_reset(self):
        self.temp_var.set(DEFAULTS["temperature"])
        self.bright_var.set(DEFAULTS["brightness"])
        self._update_labels()
        if self.enabled_var.get():
            self._apply_now()
        else:
            self._persist()

    def _persist(self):
        self.config = {
            "temperature": int(self.temp_var.get()),
            "brightness": round(float(self.bright_var.get()), 2),
            "enabled": bool(self.enabled_var.get()),
        }
        save_config(self.config)

    def _on_close(self):
        self._persist()
        self.root.destroy()


def cmd_apply_saved():
    """Headless: apply saved settings and exit. Used by autostart."""
    backend = find_backend()
    if not backend:
        return 1
    cfg = load_config()
    if cfg["enabled"]:
        apply_settings(backend, cfg["temperature"], cfg["brightness"])
    return 0


def main():
    if "--apply-saved" in sys.argv:
        return cmd_apply_saved()

    backend = find_backend()
    root = tk.Tk()
    if not backend:
        root.withdraw()
        messagebox.showerror(
            "pinight",
            "Neither redshift nor gammastep is installed.\n\n"
            "Install with:\n    sudo apt install redshift",
        )
        return 1

    PinightApp(root, backend)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
