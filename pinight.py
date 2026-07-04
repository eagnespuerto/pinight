#!/usr/bin/env python3
"""pinight — GUI control for display warmth and brightness on Raspberry Pi OS.

Wraps redshift/gammastep. Stdlib only (Tkinter) so it runs on ARMv6 (Pi Zero)
without needing pip. Works on X11 (default on 32-bit Pi OS) and Wayland.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import subprocess
import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

APP_NAME = "pinight"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "pinight.log"
PID_FILE = CONFIG_DIR / "pinight.pid"

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
        except (json.JSONDecodeError, OSError) as e:
            logging.warning("config load failed: %s; using defaults", e)
    try:
        cfg["temperature"] = max(TEMP_MIN, min(TEMP_MAX, int(cfg["temperature"])))
    except (TypeError, ValueError):
        cfg["temperature"] = DEFAULTS["temperature"]
    try:
        cfg["brightness"] = max(BRIGHT_MIN, min(BRIGHT_MAX, float(cfg["brightness"])))
    except (TypeError, ValueError):
        cfg["brightness"] = DEFAULTS["brightness"]
    cfg["enabled"] = bool(cfg["enabled"])
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def setup_logging():
    """File logging to ~/.config/pinight/pinight.log + INFO+ to stderr."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG)
    fh = logging.FileHandler(str(LOG_FILE))
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    sh.setLevel(logging.INFO)
    root.addHandler(sh)


def install_excepthook():
    def _hook(exctype, value, tb):
        logging.critical(
            "uncaught exception:\n%s",
            "".join(traceback.format_exception(exctype, value, tb)),
        )
        sys.__excepthook__(exctype, value, tb)
    sys.excepthook = _hook


def acquire_single_instance_lock():
    """Refuse to start a second GUI instance. Returns True on success."""
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
        except (OSError, ValueError):
            old = -1
        if old > 0 and old != os.getpid():
            try:
                os.kill(old, 0)
                logging.error("another pinight is running (PID %d); exiting", old)
                return False
            except OSError:
                logging.info("stale PID file (PID %d dead); clearing", old)
    try:
        PID_FILE.write_text(str(os.getpid()))
    except OSError as e:
        logging.warning("cannot write PID file %s: %s", PID_FILE, e)
    atexit.register(release_single_instance_lock)
    return True


def release_single_instance_lock():
    try:
        pid = int(PID_FILE.read_text().strip())
        if pid == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def apply_settings(backend, temperature, brightness):
    cmd = [backend, "-P", "-O", str(int(temperature)), "-b", f"{float(brightness):.2f}"]
    logging.debug("apply_settings: %s", " ".join(cmd))
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, timeout=5)
    except subprocess.TimeoutExpired:
        logging.error("apply_settings TIMED OUT after 5s")
        return
    except FileNotFoundError as e:
        logging.error("apply_settings: backend not found: %s", e)
        return
    if r.returncode != 0:
        logging.warning(
            "apply_settings rc=%d stderr=%s",
            r.returncode, r.stderr.decode(errors="replace").strip(),
        )


def reset_display(backend):
    cmd = [backend, "-x"]
    logging.debug("reset_display: %s", " ".join(cmd))
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, timeout=5)
    except subprocess.TimeoutExpired:
        logging.error("reset_display TIMED OUT after 5s")
        return
    except FileNotFoundError as e:
        logging.error("reset_display: backend not found: %s", e)
        return
    if r.returncode != 0:
        logging.warning(
            "reset_display rc=%d stderr=%s",
            r.returncode, r.stderr.decode(errors="replace").strip(),
        )


class PinightApp:
    def __init__(self, root, backend):
        self.root = root
        self.backend = backend
        self.config = load_config()
        self._debounce_job = None
        logging.info("PinightApp init backend=%s config=%s", backend, self.config)

        root.title("pinight")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

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
        logging.info("close requested")
        if self._debounce_job is not None:
            try:
                self.root.after_cancel(self._debounce_job)
            except tk.TclError:
                pass
            self._debounce_job = None
        self._persist()
        self.root.destroy()


def cmd_apply_saved():
    """Headless: apply saved settings and exit. Used by autostart."""
    backend = find_backend()
    if not backend:
        logging.error("no backend found (need redshift or gammastep)")
        return 1
    cfg = load_config()
    logging.info("apply-saved: backend=%s config=%s", backend, cfg)
    if cfg["enabled"]:
        apply_settings(backend, cfg["temperature"], cfg["brightness"])
    return 0


def main():
    setup_logging()
    install_excepthook()
    logging.info("pinight starting argv=%s pid=%d", sys.argv, os.getpid())

    if "--apply-saved" in sys.argv:
        rc = cmd_apply_saved()
        logging.info("--apply-saved done rc=%d", rc)
        return rc

    if not acquire_single_instance_lock():
        sys.stderr.write("pinight is already running. Exiting.\n")
        return 2

    backend = find_backend()
    root = tk.Tk()
    if not backend:
        logging.error("no backend found (need redshift or gammastep)")
        root.withdraw()
        messagebox.showerror(
            "pinight",
            "Neither redshift nor gammastep is installed.\n\n"
            "Install with:\n    sudo apt install redshift",
        )
        return 1

    try:
        PinightApp(root, backend)
        logging.info("entering mainloop")
        root.mainloop()
        logging.info("mainloop exited normally")
    finally:
        logging.info("pinight exiting pid=%d", os.getpid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
