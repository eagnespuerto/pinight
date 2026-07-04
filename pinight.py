#!/usr/bin/env python3
"""pinight — GUI control for display warmth and brightness on Raspberry Pi OS.

Backends (auto-detected in this order):
  1. xrandr  — X11, no location provider or DBus; most reliable on Pi OS.
  2. redshift — X11 fallback; invoked with -m randr -l manual:0:0 to skip
                 location detection (redshift's GeoClue2 path hangs on some
                 minimal Debian setups even in one-shot mode).
  3. gammastep — Wayland.

All backend commands run in a background worker thread so the Tk main
loop never blocks on a hung subprocess. Only the most-recently-requested
state is applied — rapid slider drags collapse to a single apply.

Stdlib only (Tkinter + threading) so it runs on ARMv6 (Pi Zero).
"""
from __future__ import annotations

import atexit
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
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
    "temperature": 6500,
    "brightness": 1.0,
    "enabled": False,
}

TEMP_MIN, TEMP_MAX = 2500, 6500
BRIGHT_MIN, BRIGHT_MAX = 0.3, 1.0
SUBPROCESS_TIMEOUT = 2  # seconds; redshift/xrandr normally finish in <100ms


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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


def _run(cmd, timeout=SUBPROCESS_TIMEOUT):
    """Run a subprocess with logging. Never raises."""
    logging.debug("run: %s", " ".join(cmd))
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logging.error("TIMED OUT after %ss: %s", timeout, " ".join(cmd))
        return None
    except FileNotFoundError as e:
        logging.error("not found: %s", e)
        return None
    if r.returncode != 0:
        logging.warning(
            "rc=%d cmd=%s stderr=%s",
            r.returncode, " ".join(cmd),
            r.stderr.decode(errors="replace").strip(),
        )
    return r


# ---------------------------------------------------------------------------
# Color temperature → RGB gamma (Tanner Helland approximation)
# ---------------------------------------------------------------------------

def kelvin_to_rgb(temp_k):
    """Return (r, g, b) in [0, 1] normalized so the max channel is 1.0."""
    t = temp_k / 100.0
    if t <= 66:
        r = 255.0
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
    if t <= 66:
        g = 99.4708025861 * math.log(max(t, 1)) - 161.1195681661
    else:
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    r = max(0.0, min(255.0, r)) / 255.0
    g = max(0.0, min(255.0, g)) / 255.0
    b = max(0.0, min(255.0, b)) / 255.0
    return r, g, b


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class Backend:
    name = ""

    def apply(self, temperature, brightness):
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError


class XrandrBackend(Backend):
    """X11 backend using xrandr --brightness and --gamma. No DBus, no location."""

    name = "xrandr"

    def __init__(self, outputs):
        self.outputs = outputs

    @classmethod
    def detect(cls):
        if not shutil.which("xrandr"):
            return None
        try:
            r = subprocess.run(
                ["xrandr", "--current"],
                check=False, capture_output=True, timeout=SUBPROCESS_TIMEOUT,
                text=True,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        outputs = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "connected":
                outputs.append(parts[0])
        if not outputs:
            return None
        logging.info("xrandr backend: outputs=%s", outputs)
        return cls(outputs)

    def apply(self, temperature, brightness):
        r, g, b = kelvin_to_rgb(temperature)
        gamma = f"{r:.3f}:{g:.3f}:{b:.3f}"
        bstr = f"{float(brightness):.2f}"
        for out in self.outputs:
            _run(["xrandr", "--output", out,
                  "--brightness", bstr, "--gamma", gamma])

    def reset(self):
        for out in self.outputs:
            _run(["xrandr", "--output", out,
                  "--brightness", "1.0", "--gamma", "1.0:1.0:1.0"])


class RedshiftBackend(Backend):
    """Fallback. -m randr forces X11 method; -l manual:0:0 skips GeoClue2."""

    name = "redshift"

    @classmethod
    def detect(cls):
        return cls() if shutil.which("redshift") else None

    def apply(self, temperature, brightness):
        _run([
            "redshift", "-m", "randr", "-l", "manual:0:0",
            "-P", "-O", str(int(temperature)), "-b", f"{float(brightness):.2f}",
        ])

    def reset(self):
        _run(["redshift", "-x"])


class GammastepBackend(Backend):
    """Wayland fallback."""

    name = "gammastep"

    @classmethod
    def detect(cls):
        return cls() if shutil.which("gammastep") else None

    def apply(self, temperature, brightness):
        _run([
            "gammastep", "-P", "-O", str(int(temperature)),
            "-b", f"{float(brightness):.2f}",
        ])

    def reset(self):
        _run(["gammastep", "-x"])


def find_backend():
    for cls in (XrandrBackend, RedshiftBackend, GammastepBackend):
        b = cls.detect()
        if b is not None:
            return b
    return None


# ---------------------------------------------------------------------------
# Worker thread: applies desired state in the background
# ---------------------------------------------------------------------------

class BackendWorker:
    """Applies the LATEST requested state in a background thread.

    Requests are coalesced: if the user drags a slider quickly, only the
    final position is applied. This means Tk's main loop never waits on
    a subprocess, and a hung backend can't queue up work.
    """

    def __init__(self, backend):
        self.backend = backend
        self._desired = None      # (temp, bright, enabled) or None
        self._current = None
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._loop,
                                        name="pinight-backend", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop:
            self._event.wait()
            self._event.clear()
            if self._stop:
                break
            with self._lock:
                target = self._desired
            if target is None or target == self._current:
                continue
            temp, bright, enabled = target
            try:
                if enabled:
                    self.backend.apply(temp, bright)
                else:
                    self.backend.reset()
                self._current = target
            except Exception:
                logging.exception("backend action failed")

    def request(self, temperature, brightness, enabled):
        with self._lock:
            self._desired = (int(temperature), float(brightness), bool(enabled))
        self._event.set()

    def stop(self):
        self._stop = True
        self._event.set()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class PinightApp:
    def __init__(self, root, backend):
        self.root = root
        self.backend = backend
        self.worker = BackendWorker(backend)
        self.config = load_config()
        logging.info("PinightApp init backend=%s config=%s",
                     backend.name, self.config)

        root.title("pinight")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

        # Push loaded state to worker; it'll apply if enabled.
        self.worker.request(
            self.config["temperature"],
            self.config["brightness"],
            self.config["enabled"],
        )

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=16)
        frm.grid(row=0, column=0)

        self.enabled_var = tk.BooleanVar(value=self.config["enabled"])
        ttk.Checkbutton(
            frm, text="Enable warmth and dimming", variable=self.enabled_var,
            command=self._on_change,
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
            variable=self.temp_var, command=self._on_change,
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
            variable=self.bright_var, command=self._on_change,
        ).grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            frm, text=f"dim {BRIGHT_MIN:.1f}   <->   {BRIGHT_MAX:.1f} full  (software gamma)",
            foreground="#888",
        ).grid(row=7, column=0, columnspan=2, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Button(btns, text="Reset defaults", command=self._on_reset).pack(side="left")
        ttk.Button(btns, text="Save and close", command=self._on_close).pack(side="right")

        ttk.Label(frm, text=f"backend: {self.backend.name}",
                  foreground="#888").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

        self._update_labels()

    def _update_labels(self):
        self.temp_label.config(text=f"{int(self.temp_var.get())} K")
        self.bright_label.config(text=f"{float(self.bright_var.get()):.2f}")

    def _on_change(self, *_args):
        self._update_labels()
        # Push latest state; worker coalesces to the final value.
        self.worker.request(
            self.temp_var.get(),
            self.bright_var.get(),
            self.enabled_var.get(),
        )
        self._persist()

    def _on_reset(self):
        self.temp_var.set(DEFAULTS["temperature"])
        self.bright_var.set(DEFAULTS["brightness"])
        self._on_change()

    def _persist(self):
        self.config = {
            "temperature": int(self.temp_var.get()),
            "brightness": round(float(self.bright_var.get()), 2),
            "enabled": bool(self.enabled_var.get()),
        }
        save_config(self.config)

    def _on_close(self):
        logging.info("close requested")
        self._persist()
        self.worker.stop()
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def cmd_apply_saved():
    """Headless: apply saved settings and exit. Used by autostart."""
    backend = find_backend()
    if not backend:
        logging.error("no backend available")
        return 1
    cfg = load_config()
    logging.info("apply-saved: backend=%s config=%s", backend.name, cfg)
    if cfg["enabled"]:
        backend.apply(cfg["temperature"], cfg["brightness"])
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
        logging.error("no backend available (need xrandr, redshift, or gammastep)")
        root.withdraw()
        messagebox.showerror(
            "pinight",
            "No display backend found.\n\n"
            "Install one of:\n"
            "  sudo apt install x11-xserver-utils   # xrandr (recommended)\n"
            "  sudo apt install redshift\n"
            "  sudo apt install gammastep",
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
