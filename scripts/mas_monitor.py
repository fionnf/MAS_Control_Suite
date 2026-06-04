#!/usr/bin/env python3
"""
MAS Rotor Monitor  —  v2
========================
Live dashboard for optically-detected MAS NMR rotors.

  • Reads the frequency-log CSV produced by PicoScope's built-in datalogging
    function and auto-refreshes as new rows arrive.
  • Optionally connects to an Alicat flow/pressure meter over serial and logs
    pressure, temperature, and mass-flow alongside the spin data.
  • Three-panel live view: spin frequency (time), pressure + flow (time),
    and correlation scatter plots (freq vs pressure, freq vs flow).
  • One-click export of publication-quality plots and merged CSV.

Run
---
    .venv/bin/python scripts/mas_monitor.py

Dependencies
------------
    pip install matplotlib pandas pyserial
"""

from __future__ import annotations

import csv
import threading
import time
import datetime
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox

import matplotlib
matplotlib.use("TkAgg")

import tkinter as tk
from tkinter import ttk
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    from scipy.signal import savgol_filter
    from scipy.ndimage import gaussian_filter1d
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# ── Palette ────────────────────────────────────────────────────────────────
BG        = "#191919"   # window / frame background
BG_PANEL  = "#212121"   # toolbar / control strip
BG_ENTRY  = "#272727"   # entry / well
BG_PLOT   = "#1f1f1f"   # matplotlib axes background
FG        = "#e4e4e4"   # primary text
FG_DIM    = "#5a5a5a"   # secondary / placeholder
BORDER    = "#303030"   # widget borders, spine colour
SEP_COL   = "#2c2c2c"   # divider lines

BTN_BG    = "#2b2b2b"   # button face
BTN_HV    = "#3a3a3a"   # button hover / active
LFR_BG    = "#1e1e1e"   # LabelFrame interior

ACCENT    = "#4a9fe5"   # interactive blue
GREEN     = "#3ecb6f"   # connected / OK
AMBER     = "#e8a43a"   # warning
RED       = "#e05c5c"   # error / caution
PURPLE    = "#a97ee0"   # temperature

# Data-series colours
C_FREQ    = "#4a9fe5"   # spin frequency  (blue)
C_PRESS   = "#e8a43a"   # pressure        (amber)
C_FLOW    = "#3ecb6f"   # flow rate       (green)
C_TEMP    = "#a97ee0"   # temperature     (purple)
C_MEAN    = "#e05c5c"   # mean / reference (coral)

FONT      = ("Helvetica Neue", 11)
FONT_SM   = ("Helvetica Neue", 10)
FONT_B    = ("Helvetica Neue", 11, "bold")

# ── Constants ──────────────────────────────────────────────────────────────
# Local atmospheric pressure for Zurich (~408 m altitude), in bar absolute.
# The device reports and accepts absolute pressure. This constant converts:
#   gauge → absolute:  sent_value  = typed_barg + LOCAL_ATMOS  (0 → 0 to close valve)
#   absolute → gauge:  displayed   = device_absolute - LOCAL_ATMOS
# Change this to match your site's actual barometric pressure.
LOCAL_ATMOS    = 0.953   # bar absolute, Zurich (~408 m)

APP_TITLE      = "MAS Rotor Monitor"
UNIT_MULTS     = {"Hz": 1.0, "kHz": 1e-3, "kRPM": 60e-3}
FILTER_TYPES   = ["None", "Mean", "Median", "Savitzky-Golay", "Gaussian"]
WINDOW_SIZES   = ["5", "10", "25", "50", "100", "200", "500"]
DESPIKE_THRESH = ["1.5", "2.0", "2.5", "3.0", "4.0", "5.0"]
REFRESH_SEC    = ["0.5", "1", "2", "5", "10"]
WINDOW_OPTIONS = ["All", "30 s", "1 min", "5 min", "10 min", "30 min"]
ALICAT_BAUD    = 19200
ALICAT_BAUDS   = ["9600", "19200", "38400", "57600", "115200"]
ALICAT_CSV_COLS = ["timestamp", "pressure_bar", "temperature_C",
                   "vol_flow_slm", "mass_flow_slm", "setpoint", "gas"]   # legacy

# Unified log — one file for frequency + both Alicats, always the same columns.
# Missing/disconnected channels are written as empty strings.
UNIFIED_CSV_COLS = [
    "timestamp",
    "freq_hz",
    "pressure_bar_A", "temperature_C_A", "vol_flow_slm_A", "mass_flow_slm_A", "setpoint_A", "gas_A",
    "pressure_bar_B", "temperature_C_B", "vol_flow_slm_B", "mass_flow_slm_B", "setpoint_B", "gas_B",
]

import matplotlib as _mpl
_mpl.rcParams.update({
    "font.family":      "sans-serif",
    "font.size":        8.5,
    "axes.labelsize":   8.5,
    "axes.titlesize":   9,
    "xtick.labelsize":  7.5,
    "ytick.labelsize":  7.5,
    "legend.fontsize":  7,
    "savefig.dpi":      300,
    "savefig.bbox":     "tight",
    "pdf.fonttype":     42,
    "ps.fonttype":      42,
})


# ── Widget helpers ─────────────────────────────────────────────────────────

def _group(parent, title, **kw):
    return tk.LabelFrame(
        parent, text=f"  {title}  ",
        bg=LFR_BG, fg=FG_DIM, font=FONT_SM,
        bd=1, relief="solid", **kw
    )

def _label(parent, text, dim=False, **kw):
    return tk.Label(parent, text=text, bg=BG_PANEL,
                    fg=FG_DIM if dim else FG, font=FONT, **kw)

def _label2(parent, text, dim=False, **kw):
    """Label on LFR_BG background."""
    return tk.Label(parent, text=text, bg=LFR_BG,
                    fg=FG_DIM if dim else FG, font=FONT, **kw)


class _Btn(tk.Label):
    """Dark-styled button using tk.Label so bg colour always renders on macOS.
    tk.Button ignores explicit bg in macOS native Aqua mode; tk.Label does not.
    """
    def __init__(self, parent, text, command, width=None, bg=BTN_BG, **kw):
        super().__init__(
            parent, text=text,
            bg=bg, fg=FG, font=FONT,
            padx=10, pady=4,
            relief="flat", bd=0,
            cursor="hand2",
            anchor="center",
            **kw
        )
        if width:
            self.configure(width=width)
        self._cmd      = command
        self._bg       = bg
        self._enabled  = True
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>",    self._on_enter)
        self.bind("<Leave>",    self._on_leave)

    def _on_click(self, _e):
        if self._enabled and self._cmd:
            self._cmd()

    def _on_enter(self, _e):
        if self._enabled:
            super().configure(bg=BTN_HV)

    def _on_leave(self, _e):
        super().configure(bg=self._bg)

    def configure(self, **kw):
        state = kw.pop("state", None)
        if state == "disabled":
            self._enabled = False
            super().configure(fg=FG_DIM, cursor="arrow")
        elif state in ("normal", "active"):
            self._enabled = True
            super().configure(fg=FG, cursor="hand2")
        if kw:
            super().configure(**kw)

    config = configure  # alias


def _btn(parent, text, command, width=None, **kw):
    return _Btn(parent, text, command, width=width, **kw)


def _combo(parent, var, values, width=8, **kw):
    return ttk.Combobox(parent, textvariable=var, values=values,
                        width=width, state="readonly", font=FONT, **kw)

def _entry(parent, var=None, width=18, **kw):
    e = tk.Entry(parent, bg=BG_ENTRY, fg=FG, font=FONT,
                 relief="flat", bd=1, width=width,
                 insertbackground=FG,
                 highlightbackground=BORDER, highlightthickness=1,
                 **kw)
    if var is not None:
        e.configure(textvariable=var)
    return e

def _sep_v(parent, pad=6):
    f = tk.Frame(parent, bg=BORDER, width=1)
    f.pack(side="left", fill="y", padx=pad)
    return f


# ── Axes styling ───────────────────────────────────────────────────────────

def _style_ax(ax, grid=True):
    ax.set_facecolor(BG_PLOT)
    ax.tick_params(colors=FG, labelcolor=FG, which="both", length=3)
    for sp in ax.spines.values():
        sp.set_edgecolor(BORDER)
        sp.set_linewidth(0.8)
    ax.xaxis.label.set_color(FG)
    ax.yaxis.label.set_color(FG)
    ax.title.set_color(FG)
    if grid:
        ax.grid(True, lw=0.35, alpha=0.45, color=BORDER)
        ax.set_axisbelow(True)


# ── PicoScope CSV parser ───────────────────────────────────────────────────

def load_picoscope_csv(path: str | Path) -> pd.DataFrame:
    rows: list[tuple] = []
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader, None)
        for row in reader:
            if len(row) < 2:
                continue
            try:
                ts   = datetime.datetime.strptime(row[0].strip(), "%Y-%m-%d %H:%M:%S")
                freq = float(row[1].strip().replace(",", "."))
                rows.append((ts, freq))
            except (ValueError, IndexError):
                continue

    if not rows:
        return pd.DataFrame(columns=["timestamp", "frequency_hz", "elapsed_s"])

    df = pd.DataFrame(rows, columns=["timestamp", "frequency_hz"])

    # Spread rows within each second evenly for a smooth time axis
    sub_s   = np.zeros(len(df))
    gs, prev_ts = 0, None
    for i, ts in enumerate(df["timestamp"]):
        if ts != prev_ts:
            if prev_ts is not None:
                n = i - gs
                sub_s[gs:i] = np.linspace(0, 1 - 1 / n, n)
            gs, prev_ts = i, ts
    n = len(df) - gs
    if n > 0:
        sub_s[gs:] = np.linspace(0, 1 - 1 / n, n)

    df["timestamp"] = (pd.to_datetime(df["timestamp"])
                       + pd.to_timedelta(sub_s, unit="s"))
    df["elapsed_s"] = (df["timestamp"] - df["timestamp"].iloc[0]).dt.total_seconds()
    return df


# ── Alicat CSV file loader ─────────────────────────────────────────────────

def load_alicat_csv(path: str | Path, unit: str = "A") -> pd.DataFrame:
    """Load a unified or legacy Alicat log CSV and normalise column names to
    match the live-polling format used internally (pressure, temperature,
    vol_flow, mass_flow, setpoint, gas).

    *unit* selects which Alicat to extract from a unified file ("A" or "B").
    """
    df = pd.read_csv(path, encoding="utf-8-sig", on_bad_lines="skip")

    if "timestamp" not in df.columns:
        raise ValueError("CSV does not contain a 'timestamp' column.")

    # Detect unified format (has _A / _B suffixed columns)
    if f"pressure_bar_{unit}" in df.columns:
        suffix = f"_{unit}"
        rename = {
            f"pressure_bar{suffix}":  "pressure",
            f"temperature_C{suffix}": "temperature",
            f"vol_flow_slm{suffix}":  "vol_flow",
            f"mass_flow_slm{suffix}": "mass_flow",
            f"setpoint{suffix}":      "setpoint",
            f"gas{suffix}":           "gas",
        }
    else:
        # Legacy single-unit format
        rename = {
            "pressure_bar":  "pressure",
            "temperature_C": "temperature",
            "vol_flow_slm":  "vol_flow",
            "mass_flow_slm": "mass_flow",
        }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    for col in ("pressure", "temperature", "vol_flow", "mass_flow"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ── Alicat serial logger ───────────────────────────────────────────────────

class AlicatLogger:
    def __init__(self, port: str, address: str = "A", poll_hz: float = 2.0):
        self.port    = port
        self.address = address.upper()
        self.poll_hz = poll_hz
        self._conn: "serial.Serial | None" = None
        self._thread: threading.Thread | None = None
        self._stop   = threading.Event()
        self._lock   = threading.Lock()
        self._data: deque[dict] = deque(maxlen=100_000)
        self.last_reading: dict | None = None
        self.last_raw:     str  = ""
        self.error:        str | None = None
        self._raw_log: deque[str] = deque(maxlen=200)   # rolling raw-line history
        # CSV file logging
        self._csv_fh     = None
        self._csv_writer = None
        self._csv_rows   = 0
        # Thread-safe command queue (setpoint / gas changes)
        self._cmd_lock    = threading.Lock()
        self._pending_cmds: list[str] = []   # queue; sent FIFO before next poll

    def connect(self, baud: int = ALICAT_BAUD) -> None:
        if not HAS_SERIAL:
            raise RuntimeError("pyserial not installed — run: pip install pyserial")
        self._conn = serial.Serial(
            self.port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,        # generous read timeout
            write_timeout=1.0,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )
        time.sleep(0.1)                    # let adapter settle after open
        self._conn.reset_input_buffer()    # discard any stale bytes
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self.stop_csv_log()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._conn and self._conn.is_open:
            self._conn.close()
        self._conn = None

    def start_csv_log(self, path: str | Path) -> None:
        """Open *path* for writing and begin appending every poll reading."""
        self.stop_csv_log()             # close any previous file
        self._csv_fh = open(path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_fh)
        self._csv_writer.writerow(ALICAT_CSV_COLS)   # header
        self._csv_fh.flush()
        self._csv_rows = 0

    def stop_csv_log(self) -> None:
        """Flush and close the Alicat CSV log file (no-op if not open)."""
        if self._csv_fh is not None:
            try:
                self._csv_fh.flush()
                self._csv_fh.close()
            except Exception:
                pass
            self._csv_fh     = None
            self._csv_writer = None

    @property
    def csv_rows_written(self) -> int:
        return self._csv_rows

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and self._conn.is_open

    def _queue(self, *cmds: str) -> None:
        """Append one or more ASCII commands to the send queue (thread-safe)."""
        with self._cmd_lock:
            self._pending_cmds.extend(cmds)

    def set_setpoint(self, value: float) -> None:
        """Queue a setpoint change (sent before the next poll cycle)."""
        if not self.is_connected:
            raise RuntimeError("Not connected to Alicat.")
        self._queue(f"{self.address}S{value:.4f}\r")

    def set_ramp_rate(self, rate: float) -> None:
        """Queue a ramp-rate change on the Alicat device (slm/s or bar/s).
        Sends: <address>SR<value>  e.g. 'ASR0.100'
        Set rate=0 to disable the device's internal ramp."""
        if not self.is_connected:
            raise RuntimeError("Not connected to Alicat.")
        self._queue(f"{self.address}SR{rate:.4f}\r")

    def set_ramp_rate_and_setpoint(self, rate: float, value: float) -> None:
        """Atomically queue ramp-rate then setpoint as back-to-back commands."""
        if not self.is_connected:
            raise RuntimeError("Not connected to Alicat.")
        self._queue(
            f"{self.address}SR{rate:.4f}\r",
            f"{self.address}S{value:.4f}\r",
        )

    def set_gas(self, gas_id: int) -> None:
        """Queue a gas-type change by integer ID (0=Air, 1=Ar, 4=CO2, 8=N2, …)."""
        if not self.is_connected:
            raise RuntimeError("Not connected to Alicat.")
        self._queue(f"{self.address}$$G{gas_id:d}\r")

    def get_dataframe(self) -> pd.DataFrame:
        with self._lock:
            rows = list(self._data)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _loop(self) -> None:
        interval = 1.0 / self.poll_hz
        while not self._stop.is_set():
            try:
                # Drain queued commands (setpoint / ramp-rate / gas change)
                with self._cmd_lock:
                    cmds, self._pending_cmds = self._pending_cmds[:], []
                for cmd in cmds:
                    self._conn.reset_input_buffer()
                    self._conn.rts = True
                    self._conn.write(cmd.encode())
                    self._conn.flush()
                    time.sleep(0.01)
                    self._conn.rts = False
                    time.sleep(0.05)
                    self._conn.read_until(b"\r")   # discard echo / ack

                r = self._poll()
                if r:
                    with self._lock:
                        self._data.append(r)
                    self.last_reading = r
                    self.error = None
                    # Write to CSV if logging is active
                    if self._csv_writer is not None:
                        self._csv_writer.writerow([
                            r.get("timestamp", ""),
                            r.get("pressure", ""),
                            r.get("temperature", ""),
                            r.get("vol_flow", ""),
                            r.get("mass_flow", ""),
                            r.get("setpoint", ""),
                            r.get("gas", ""),
                        ])
                        self._csv_fh.flush()
                        self._csv_rows += 1
            except Exception as exc:
                self.error = str(exc)
            time.sleep(interval)

    def _poll(self) -> dict | None:
        self._conn.reset_input_buffer()

        # Toggle RTS high before write — many USB-RS485 adapters use RTS to
        # switch the DE (Driver Enable) line on the half-duplex transceiver.
        self._conn.rts = True
        self._conn.write((self.address + "\r").encode())
        self._conn.flush()
        # Hold RTS long enough for the last stop-bit to finish transmitting
        # at 19200 baud one char ≈ 0.52 ms; 2 chars + margin = 10 ms
        time.sleep(0.01)
        self._conn.rts = False                    # switch adapter to receive mode
        time.sleep(0.05)                          # DE→RE turnaround settle

        # Alicat responses are terminated with \r, NOT \r\n.
        # readline() waits for \n and would time out — use read_until instead.
        raw_bytes = self._conn.read_until(b"\r")
        raw = raw_bytes.decode("ascii", errors="replace").strip()
        # Store hex preview for diagnostics so null/garbage bytes are visible
        hex_preview = raw_bytes[:16].hex(" ") if raw_bytes else ""
        self.last_raw = raw if raw else f"(hex: {hex_preview})"
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        rx_repr = repr(raw) if raw else repr(hex_preview)
        self._raw_log.append(
            f"{ts}  TX: {self.address!r}\\r   RX({len(raw_bytes)}B): {rx_repr}"
        )
        if not raw:
            return None
        parts = raw.split()
        # Need at least address + one numeric field; be lenient about response length
        if len(parts) < 2:
            return None
        reading: dict = {"timestamp": datetime.datetime.now(), "address": parts[0]}
        # Standard Alicat streaming response (7 fields after address):
        #   <addr> <pressure> <temp> <vol_flow> <mass_flow> <setpoint> <gas>
        # Map whatever fields are present; extras are silently ignored.
        for label, val in zip(
            ["pressure", "temperature", "vol_flow", "mass_flow", "setpoint", "gas"],
            parts[1:]
        ):
            try:
                reading[label] = float(val)
            except ValueError:
                reading[label] = val
        # Accept only if we got at least one numeric value (pressure or mass_flow)
        if "pressure" not in reading and "mass_flow" not in reading:
            return None
        return reading


# ── Main application ───────────────────────────────────────────────────────

class MASMonitor(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.geometry("1240x760")
        self.minsize(900, 600)

        self._csv_path: Path | None = None
        self._df       = pd.DataFrame()

        # Two Alicat instances; index 0 = Alicat A, index 1 = Alicat B
        self._alicats: list[AlicatLogger] = [AlicatLogger(""), AlicatLogger("")]
        self._alicat = self._alicats[0]   # backward-compat alias

        # Unified log (frequency + both Alicats → one CSV)
        self._unified_logging:    bool          = False
        self._unified_csv_path:   Path | None   = None
        self._unified_log_fh                    = None
        self._unified_log_writer                = None
        self._unified_log_rows:   int           = 0

        # Shared file-load (unified or legacy Alicat CSV for scatter/export)
        self._alicat_file_df:  pd.DataFrame = pd.DataFrame()
        self._alicat_file_path: Path | None = None

        # Per-Alicat UI widget dicts (populated by _build_alicat_unit)
        self._alicat_ui: list[dict] = [{}, {}]

        self._refresh_job: str | None = None
        self._live_redraw_job: str | None = None

        # Spin routine state
        # Each step: (setpoint_barg, duration_seconds)
        self._spinup_steps:   list[tuple] = []
        self._spindown_steps: list[tuple] = []
        self._routine_thread: threading.Thread | None = None
        self._routine_stop   = threading.Event()
        self._routine_pause  = threading.Event()
        self._routine_status = ""   # written by thread, read by UI tick
        self._ramp_rate_var  = tk.StringVar(value="0.1")   # barg/s; 0 = instant


        # ttk style overrides for comboboxes — keep default Aqua theme so that
        # tk.Button respects explicit bg colours; only restyle TCombobox fields.
        st = ttk.Style(self)
        st.configure("TCombobox",
                      fieldbackground=BG_ENTRY,
                      background=BTN_BG,
                      foreground=FG,
                      selectbackground=ACCENT,
                      selectforeground=FG,
                      bordercolor=BORDER,
                      arrowcolor=FG_DIM)
        # Make the drop-down list background dark
        self.option_add("*TCombobox*Listbox.background", BG_ENTRY)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", FG)

        self._build_ui()
        self._poll_alicat_status()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_plot()      # row 0 — expands
        self._build_controls()  # row 1
        self._build_status()    # row 2
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    # -- plot --------------------------------------------------------------

    def _build_plot(self):
        pf = tk.Frame(self, bg=BG, padx=4, pady=3)
        pf.grid(row=0, column=0, sticky="nsew")
        pf.rowconfigure(0, weight=1)
        pf.columnconfigure(0, weight=1)

        self._fig = Figure(facecolor=BG, tight_layout=False)
        self._fig.set_layout_engine("none")
        self._canvas = FigureCanvasTkAgg(self._fig, master=pf)
        self._canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        tb_frame = tk.Frame(pf, bg=BG_PANEL)
        tb_frame.grid(row=1, column=0, sticky="ew")
        NavigationToolbar2Tk(self._canvas, tb_frame)

    # -- controls panel (tabbed) -------------------------------------------

    def _build_controls(self):
        outer = tk.Frame(self, bg=BG_PANEL)
        outer.grid(row=1, column=0, sticky="ew")
        outer.columnconfigure(0, weight=1)

        # ── Tab bar ──
        tab_bar = tk.Frame(outer, bg=BG_PANEL)
        tab_bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 0))

        content = tk.Frame(outer, bg=BG_PANEL)
        content.grid(row=1, column=0, sticky="ew")
        content.columnconfigure(0, weight=1)

        self._tab_frames: dict[str, tk.Frame] = {}
        self._tab_btns:   dict[str, tk.Label] = {}

        def _show_tab(name: str):
            for n, f in self._tab_frames.items():
                f.grid_remove()
            self._tab_frames[name].grid(row=0, column=0, sticky="ew")
            for n, b in self._tab_btns.items():
                b.configure(bg=BTN_BG)
                b._bg = BTN_BG
            self._tab_btns[name].configure(bg=ACCENT)
            self._tab_btns[name]._bg = ACCENT

        def _make_tab(label: str) -> tk.Frame:
            btn = _Btn(tab_bar, text=label, command=lambda l=label: _show_tab(l),
                       bg=BTN_BG)
            btn.pack(side="left", padx=(0, 2))
            frame = tk.Frame(content, bg=BG_PANEL, padx=8, pady=6)
            frame.columnconfigure(0, weight=1)
            self._tab_frames[label] = frame
            self._tab_btns[label]   = btn
            return frame

        freq_tab     = _make_tab("Frequency")
        gas_tab      = _make_tab("Gas & Control")
        log_tab      = _make_tab("Logging")
        exp_tab      = _make_tab("Export")
        routine_tab  = _make_tab("Routines")

        # ════════════════════════════════════════════════
        # TAB 1 — Frequency display settings
        # ════════════════════════════════════════════════
        strip = freq_tab
        strip.columnconfigure(0, weight=0)

        row = tk.Frame(strip, bg=BG_PANEL); row.pack(fill="x")

        # ── CSV file ──
        fg_ = _group(row, "Spin frequency CSV")
        fg_.pack(side="left", padx=(0, 10), fill="y", pady=2)

        rf = tk.Frame(fg_, bg=LFR_BG); rf.pack(fill="x", padx=8, pady=(6, 2))
        self._file_lbl = tk.Label(
            rf, text="  no file loaded  ",
            bg=BG_ENTRY, fg=FG_DIM, font=FONT_SM,
            relief="flat", bd=0, padx=4, pady=1,
            anchor="w", width=30,
            highlightbackground=BORDER, highlightthickness=1,
        )
        self._file_lbl.pack(side="left", padx=(0, 6))
        _btn(rf, "Open…", self._open_csv).pack(side="left", padx=(0, 4))
        _btn(rf, "✕", lambda: self._clear_csv(), width=2).pack(side="left")

        ra = tk.Frame(fg_, bg=LFR_BG); ra.pack(fill="x", padx=8, pady=(2, 8))
        _label2(ra, "Auto-refresh:").pack(side="left")
        self._refresh_var = tk.StringVar(value="2")
        _combo(ra, self._refresh_var, REFRESH_SEC, width=4).pack(side="left", padx=(5, 3))
        _label2(ra, "s").pack(side="left", padx=(0, 8))
        self._auto_var = tk.BooleanVar(value=False)
        self._auto_chk = tk.Checkbutton(
            ra, text="Auto", variable=self._auto_var,
            bg=LFR_BG, fg=FG, font=FONT,
            selectcolor=BG_ENTRY, activebackground=LFR_BG, activeforeground=FG,
            command=self._toggle_auto)
        self._auto_chk.pack(side="left", padx=(0, 8))
        _btn(ra, "Refresh", self._reload).pack(side="left")

        # ── Display ──
        dg = _group(row, "Display")
        dg.pack(side="left", padx=(0, 10), fill="y", pady=2)

        r = tk.Frame(dg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=(6, 2))
        _label2(r, "Unit:").pack(side="left")
        self._unit_var = tk.StringVar(value="kHz")
        uc = _combo(r, self._unit_var, list(UNIT_MULTS), width=6)
        uc.pack(side="left", padx=(6, 0))
        uc.bind("<<ComboboxSelected>>", lambda _: self._redraw())

        r = tk.Frame(dg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=2)
        _label2(r, "Filter:").pack(side="left")
        self._filter_var = tk.StringVar(value="None")
        ft = _combo(r, self._filter_var, FILTER_TYPES, width=13)
        ft.pack(side="left", padx=(6, 6))
        ft.bind("<<ComboboxSelected>>", lambda _: self._redraw())
        _label2(r, "N:").pack(side="left")
        self._fwin_var = tk.StringVar(value="25")
        fw = _combo(r, self._fwin_var, WINDOW_SIZES, width=4)
        fw.pack(side="left", padx=(4, 0))
        fw.bind("<<ComboboxSelected>>", lambda _: self._redraw())

        r = tk.Frame(dg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=2)
        self._despike_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="Despike", variable=self._despike_var,
            bg=LFR_BG, fg=FG, font=FONT, selectcolor=BG_ENTRY,
            activebackground=LFR_BG, activeforeground=FG,
            command=self._redraw).pack(side="left")
        _label2(r, "  thresh:").pack(side="left")
        self._despike_thresh_var = tk.StringVar(value="3.0")
        dt = _combo(r, self._despike_thresh_var, DESPIKE_THRESH, width=4)
        dt.pack(side="left", padx=(4, 0))
        dt.bind("<<ComboboxSelected>>", lambda _: self._redraw())
        _label2(r, "σ").pack(side="left", padx=(3, 0))

        r = tk.Frame(dg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=2)
        self._show_mean_var  = tk.BooleanVar(value=True)
        self._show_sigma_var = tk.BooleanVar(value=True)
        tk.Checkbutton(r, text="Mean line", variable=self._show_mean_var,
            bg=LFR_BG, fg=FG, font=FONT, selectcolor=BG_ENTRY,
            activebackground=LFR_BG, activeforeground=FG,
            command=self._redraw).pack(side="left", padx=(0, 10))
        tk.Checkbutton(r, text="±1σ band", variable=self._show_sigma_var,
            bg=LFR_BG, fg=FG, font=FONT, selectcolor=BG_ENTRY,
            activebackground=LFR_BG, activeforeground=FG,
            command=self._redraw).pack(side="left")

        r = tk.Frame(dg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=(2, 8))
        _label2(r, "Show last:").pack(side="left")
        self._window_var = tk.StringVar(value="All")
        wc = _combo(r, self._window_var, WINDOW_OPTIONS, width=7)
        wc.pack(side="left", padx=(6, 0))
        wc.bind("<<ComboboxSelected>>", lambda _: self._redraw())

        # ── Frequency limits ──
        lg = _group(row, "Freq limits")
        lg.pack(side="left", padx=(0, 10), fill="y", pady=2)

        r = tk.Frame(lg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=(6, 2))
        self._freq_lim_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="Enable", variable=self._freq_lim_var,
            bg=LFR_BG, fg=FG, font=FONT, selectcolor=BG_ENTRY,
            activebackground=LFR_BG, activeforeground=FG,
            command=self._redraw).pack(side="left", padx=(0, 6))

        r = tk.Frame(lg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=2)
        _label2(r, "Low:").pack(side="left")
        self._freq_lo_var = tk.StringVar(value="0")
        lo_e = _entry(r, self._freq_lo_var, width=9)
        lo_e.pack(side="left", padx=(4, 2))
        lo_e.bind("<Return>", lambda _: self._redraw())
        lo_e.bind("<FocusOut>", lambda _: self._redraw())

        r = tk.Frame(lg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=2)
        _label2(r, "High:").pack(side="left")
        self._freq_hi_var = tk.StringVar(value="1e9")
        hi_e = _entry(r, self._freq_hi_var, width=9)
        hi_e.pack(side="left", padx=(4, 2))
        hi_e.bind("<Return>", lambda _: self._redraw())
        hi_e.bind("<FocusOut>", lambda _: self._redraw())

        r = tk.Frame(lg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=(2, 4))
        _label2(r, "Unit:").pack(side="left")
        self._freq_lim_unit_var = tk.StringVar(value="Hz")
        lu = _combo(r, self._freq_lim_unit_var, ["Hz", "kHz", "kRPM"], width=6)
        lu.pack(side="left", padx=(4, 0))
        lu.bind("<<ComboboxSelected>>", lambda _: self._redraw())

        r = tk.Frame(lg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=(0, 8))
        self._freq_lim_count_lbl = tk.Label(r, text="", bg=LFR_BG, fg=FG_DIM, font=FONT_SM)
        self._freq_lim_count_lbl.pack(side="left")

        # ════════════════════════════════════════════════
        # TAB 2 — Gas & Pressure Control
        # Layout: [Connection A+B] | [Control A] | [Control B]
        # ════════════════════════════════════════════════
        row2 = tk.Frame(gas_tab, bg=BG_PANEL); row2.pack(fill="x")

        # Initialise per-unit dicts up front
        for i in range(2):
            if not self._alicat_ui[i]:
                self._alicat_ui[i] = {}
            d = self._alicat_ui[i]
            d.setdefault("pressure_offset", LOCAL_ATMOS)
            d.setdefault("sp_ramp_thread",  None)
            d.setdefault("sp_ramp_stop",    threading.Event())
            d.setdefault("valve_after_id",  None)
            d.setdefault("valve_press_time", None)

        # ── Shared Connection column ──
        cxg = _group(row2, "Connection")
        cxg.pack(side="left", padx=(0, 8), fill="y", pady=2)

        if not HAS_SERIAL:
            tk.Label(cxg, text="pip install pyserial to enable",
                     bg=LFR_BG, fg=FG_DIM, font=FONT_SM).pack(padx=10, pady=10)
        else:
            for i in range(2):
                lbl_name = f"Alicat {'A' if i == 0 else 'B'}"
                ui = self._alicat_ui[i]
                fg_c = C_FLOW if i == 0 else C_PRESS

                hr = tk.Frame(cxg, bg=LFR_BG)
                hr.pack(fill="x", padx=8, pady=(6 if i == 0 else 6, 2))
                tk.Label(hr, text=lbl_name, bg=LFR_BG, fg=fg_c,
                         font=FONT_B, anchor="w").pack(side="left", padx=(0, 8))
                ui["alicat_lbl"] = tk.Label(hr, text="Not connected",
                    bg=LFR_BG, fg=FG_DIM, font=FONT_SM, anchor="w")
                ui["alicat_lbl"].pack(side="left")

                r0 = tk.Frame(cxg, bg=LFR_BG); r0.pack(fill="x", padx=8, pady=(0, 2))
                _label2(r0, "Port:").pack(side="left")
                ui["port_var"] = tk.StringVar()
                ui["port_cb"]  = _combo(r0, ui["port_var"], [], width=14)
                ui["port_cb"].pack(side="left", padx=(6, 4))
                _btn(r0, "⟳", lambda ii=i: self._scan_ports(ii), width=2).pack(side="left")
                _label2(r0, "  Baud:").pack(side="left")
                ui["baud_var"] = tk.StringVar(value="19200")
                _combo(r0, ui["baud_var"], ALICAT_BAUDS, width=7).pack(side="left", padx=(4, 6))
                _label2(r0, "Addr:").pack(side="left")
                ui["addr_var"] = tk.StringVar(value="A" if i == 0 else "B")
                _entry(r0, ui["addr_var"], width=3).pack(side="left", padx=(4, 0))

                r1 = tk.Frame(cxg, bg=LFR_BG); r1.pack(fill="x", padx=8, pady=(0, 2))
                ui["conn_btn"] = _btn(r1, "Connect", lambda ii=i: self._toggle_alicat_conn(ii))
                ui["conn_btn"].pack(side="left", padx=(0, 6))
                _btn(r1, "Serial monitor…",
                     lambda ii=i: self._open_serial_monitor(ii)).pack(side="left")

                if i == 0:
                    tk.Frame(cxg, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(6, 0))

            tk.Frame(cxg, bg=LFR_BG, height=4).pack()

        # ── Per-unit Control columns ──
        self._build_alicat_unit(row2, 0)
        self._build_alicat_unit(row2, 1)
        if self._alicat_ui[1].get("panel_frame"):
            self._alicat_ui[1]["panel_frame"].pack_forget()

        # ════════════════════════════════════════════════
        # TAB 3 — Logging
        # ════════════════════════════════════════════════
        log_row = tk.Frame(log_tab, bg=BG_PANEL); log_row.pack(fill="x")

        ulg = _group(log_row, "Unified log  (freq + Alicat A + Alicat B)")
        ulg.pack(side="left", padx=(0, 10), fill="y", pady=2)

        tk.Label(ulg, text="One CSV, always the same columns — log everything at once.",
                 bg=LFR_BG, fg=FG_DIM, font=FONT_SM, anchor="w").pack(
                 fill="x", padx=8, pady=(6, 4))

        r_ulog = tk.Frame(ulg, bg=LFR_BG); r_ulog.pack(fill="x", padx=8, pady=(0, 2))
        _label2(r_ulog, "File:").pack(side="left")
        self._unified_log_lbl = tk.Label(
            r_ulog, text="  not set  ",
            bg=BG_ENTRY, fg=FG_DIM, font=FONT_SM,
            relief="flat", bd=0, padx=4, pady=1, anchor="w", width=30,
            highlightbackground=BORDER, highlightthickness=1)
        self._unified_log_lbl.pack(side="left", padx=(4, 4))
        _btn(r_ulog, "Browse…", self._browse_unified_log).pack(side="left")

        r_ulog2 = tk.Frame(ulg, bg=LFR_BG); r_ulog2.pack(fill="x", padx=8, pady=(2, 4))
        self._unified_log_btn = _btn(r_ulog2, "▶  Start logging", self._toggle_unified_log)
        self._unified_log_btn.pack(side="left", padx=(0, 8))
        self._unified_log_row_lbl = tk.Label(r_ulog2, text="", bg=LFR_BG,
                                              fg=FG_DIM, font=FONT_SM)
        self._unified_log_row_lbl.pack(side="left")

        tk.Label(ulg,
                 text="Columns: timestamp · freq_hz · pressure/temp/flow/setpoint/gas for A and B",
                 bg=LFR_BG, fg=FG_DIM, font=FONT_SM, anchor="w").pack(
                 fill="x", padx=8, pady=(4, 8))

        # ── Load historical file ──
        hg = _group(log_row, "Load historical log")
        hg.pack(side="left", fill="y", pady=2)

        tk.Label(hg, text="Load a unified or legacy Alicat CSV to overlay on plots.",
                 bg=LFR_BG, fg=FG_DIM, font=FONT_SM, anchor="w").pack(
                 fill="x", padx=8, pady=(6, 4))

        r_hist2 = tk.Frame(hg, bg=LFR_BG); r_hist2.pack(fill="x", padx=8, pady=(0, 8))
        _label2(r_hist2, "File:").pack(side="left")
        self._alicat_file_lbl2 = tk.Label(
            r_hist2, text="  no file loaded  ",
            bg=BG_ENTRY, fg=FG_DIM, font=FONT_SM,
            relief="flat", bd=0, padx=4, pady=1, anchor="w", width=30,
            highlightbackground=BORDER, highlightthickness=1)
        self._alicat_file_lbl2.pack(side="left", padx=(4, 4))
        _btn(r_hist2, "Open…", self._open_alicat_file).pack(side="left", padx=(0, 4))
        _btn(r_hist2, "✕", self._clear_alicat_file, width=2).pack(side="left")

        # ════════════════════════════════════════════════
        # TAB 4 — Export
        # ════════════════════════════════════════════════
        eg = _group(exp_tab, "Export")
        eg.pack(side="left", fill="y", pady=2, padx=(0, 10))

        r = tk.Frame(eg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=(6, 2))
        _label2(r, "From:").pack(side="left")
        self._t_from = _entry(r, width=18); self._t_from.pack(side="left", padx=(6, 10))
        _label2(r, "To:").pack(side="left")
        self._t_to   = _entry(r, width=18); self._t_to.pack(side="left", padx=(6, 10))
        _btn(r, "Use visible", self._use_visible_range).pack(side="left")

        r = tk.Frame(eg, bg=LFR_BG); r.pack(fill="x", padx=8, pady=(2, 8))
        _btn(r, "Save plot (PDF)", lambda: self._export_plot("pdf")).pack(side="left", padx=(0, 6))
        _btn(r, "Save plot (PNG)", lambda: self._export_plot("png")).pack(side="left", padx=(0, 6))
        _btn(r, "Save data (CSV)", self._export_csv).pack(side="left")

        # ════════════════════════════════════════════════
        # TAB 4 — Routines
        # ════════════════════════════════════════════════
        rg = _group(routine_tab, "Spin routine")
        rg.pack(side="left", padx=(0, 10), fill="y", pady=2)

        rb = tk.Frame(rg, bg=LFR_BG); rb.pack(fill="x", padx=8, pady=(6, 4))
        _btn(rb, "↑ Spin up",   self._start_spinup,   bg="#1e3d1e").pack(side="left", padx=(0, 6))
        _btn(rb, "↓ Spin down", self._start_spindown,  bg="#1e2a3d").pack(side="left", padx=(0, 6))
        self._pause_btn = _btn(rb, "⏸ Pause", self._pause_routine, bg=BTN_BG)
        self._pause_btn.pack(side="left", padx=(0, 6))
        _btn(rb, "⏹ Stop", self._stop_routine, bg="#3d1e1e").pack(side="left")

        rb2 = tk.Frame(rg, bg=LFR_BG); rb2.pack(fill="x", padx=8, pady=(0, 2))
        _btn(rb2, "Edit routines…", self._open_routine_editor).pack(side="left", padx=(0, 12))
        _label2(rb2, "Ramp:").pack(side="left")
        _entry(rb2, self._ramp_rate_var, width=5).pack(side="left", padx=(4, 4))
        _label2(rb2, "barg/s  (0 = instant)").pack(side="left")

        self._routine_lbl = tk.Label(
            rg, text="No routine running", bg=LFR_BG, fg=FG_DIM,
            font=FONT_SM, anchor="w")
        self._routine_lbl.pack(fill="x", padx=8, pady=(2, 8))

        # Show Frequency tab by default
        _show_tab("Frequency")

    # -- status bar --------------------------------------------------------

    def _build_status(self):
        self._status_var = tk.StringVar(value="Ready — open a PicoScope CSV file to begin.")
        bar = tk.Label(self, textvariable=self._status_var,
                       bg=BG_PANEL, fg=FG_DIM, font=FONT_SM,
                       anchor="w", padx=10, pady=4,
                       relief="flat")
        bar.grid(row=2, column=0, sticky="ew")

    # ── Alicat unit builder ────────────────────────────────────────────────

    def _build_alicat_unit(self, parent, idx: int):
        """Build the pressure-control panel for Alicat index idx.
        Connection/logging widgets are built in the shared panels above."""
        label_name = f"Alicat {'A' if idx == 0 else 'B'}"
        ui = self._alicat_ui[idx]   # already initialised by _build_controls

        ui["panel_frame"] = tk.Frame(parent, bg=BG_PANEL)
        ui["panel_frame"].pack(side="left", padx=(0, 10), fill="y", pady=2)
        panel = ui["panel_frame"]

        # ── Pressure Control group ──
        cg = _group(panel, f"{label_name} pressure control")
        cg.pack(fill="x")

        ro = tk.Frame(cg, bg=LFR_BG); ro.pack(fill="x", padx=8, pady=(6, 2))

        def _tile(par, title, col):
            f = tk.Frame(par, bg=BG_ENTRY, bd=0,
                         highlightbackground=BORDER, highlightthickness=1)
            f.grid_columnconfigure(0, weight=1)
            tk.Label(f, text=title, bg=BG_ENTRY, fg=FG_DIM,
                     font=FONT_SM, anchor="w").pack(fill="x", padx=5, pady=(3, 0))
            val_lbl = tk.Label(f, text="–", bg=BG_ENTRY, fg=col,
                               font=("Helvetica Neue", 13, "bold"), anchor="w")
            val_lbl.pack(fill="x", padx=5, pady=(0, 4))
            return f, val_lbl

        f_p,  ui["ctl_p_lbl"]  = _tile(ro, "Pressure",  C_PRESS)
        f_t,  ui["ctl_t_lbl"]  = _tile(ro, "Temp",      C_TEMP)
        f_q,  ui["ctl_q_lbl"]  = _tile(ro, "Mass flow", C_FLOW)
        f_sp, ui["ctl_sp_lbl"] = _tile(ro, "Setpoint",  FG)
        for col_i, tile in enumerate([f_p, f_t, f_q, f_sp]):
            tile.grid(row=0, column=col_i, padx=(0, 4) if col_i < 3 else 0, pady=0, sticky="ew")
            ro.columnconfigure(col_i, weight=1)

        ru = tk.Frame(cg, bg=LFR_BG); ru.pack(fill="x", padx=8, pady=(0, 2))
        for col_i, txt in enumerate(["barg", "°C", "slm", "barg"]):
            tk.Label(ru, text=txt, bg=LFR_BG, fg=FG_DIM, font=("Helvetica Neue", 9),
                     anchor="center").grid(row=0, column=col_i, sticky="ew",
                                           padx=(0, 4) if col_i < 3 else 0)
            ru.columnconfigure(col_i, weight=1)

        rs = tk.Frame(cg, bg=LFR_BG); rs.pack(fill="x", padx=8, pady=(4, 2))
        _label2(rs, "Set SP:").pack(side="left")
        ui["sp_entry_var"] = tk.StringVar(value="0.0000")
        sp_entry = _entry(rs, ui["sp_entry_var"], width=9)
        sp_entry.pack(side="left", padx=(6, 6))
        sp_entry.bind("<Return>", lambda _, i=idx: self._send_setpoint(i))
        _btn(rs, "Send", lambda i=idx: self._send_setpoint(i)).pack(side="left", padx=(0, 8))
        _label2(rs, "Ramp:").pack(side="left")
        ui["sp_ramp_var"] = tk.StringVar(value="0.5")
        _entry(rs, ui["sp_ramp_var"], width=5).pack(side="left", padx=(4, 2))
        _label2(rs, "barg/s").pack(side="left", padx=(0, 8))
        _label2(rs, "Gas:").pack(side="left")
        ui["gas_var"] = tk.StringVar(value="N2")
        # Plain-ASCII labels so every gas (incl. N2) renders regardless of the
        # combobox font — Unicode subscripts can fail to display on some systems.
        ALICAT_GASES = ["Air", "Ar", "CH4", "CO", "CO2", "C2H6", "H2", "He", "N2"]
        ui["gas_cb"] = _combo(rs, ui["gas_var"], ALICAT_GASES, width=6)
        ui["gas_cb"].pack(side="left", padx=(4, 0))
        ui["gas_cb"].bind("<<ComboboxSelected>>", lambda _, i=idx: self._send_gas(i))

        rz = tk.Frame(cg, bg=LFR_BG); rz.pack(fill="x", padx=8, pady=(2, 2))
        _btn(rz, "Set Zero", lambda i=idx: self._set_zero_from_reading(i),
             bg="#1e2a3d").pack(side="left", padx=(0, 4))
        _btn(rz, "Clear offset", lambda i=idx: self._clear_pressure_offset(i)).pack(side="left", padx=(0, 8))
        ui["pressure_offset"] = LOCAL_ATMOS
        ui["zero_lbl"] = tk.Label(rz, text=f"offset: {ui['pressure_offset']:.5f} bar",
                                   bg=LFR_BG, fg=FG_DIM, font=FONT_SM, anchor="w")
        ui["zero_lbl"].pack(side="left")

        # Valve Off — press-and-hold 2 s to fire
        rv = tk.Frame(cg, bg=LFR_BG); rv.pack(fill="x", padx=8, pady=(2, 2))
        ui["valve_btn"] = tk.Label(
            rv, text="⛔  Valve OFF  (hold 2 s)",
            bg="#3d1e1e", fg=FG, font=FONT,
            padx=10, pady=4, relief="flat", cursor="hand2", anchor="center")
        ui["valve_btn"].pack(side="left")
        ui["valve_hint"] = tk.Label(rv, text="", bg=LFR_BG, fg=FG_DIM, font=FONT_SM)
        ui["valve_hint"].pack(side="left", padx=(8, 0))
        ui["valve_after_id"] = None
        ui["valve_press_time"] = None
        ui["sp_ramp_thread"] = None
        ui["sp_ramp_stop"] = threading.Event()

        def _valve_press(event, i=idx):
            ui2 = self._alicat_ui[i]
            ui2["valve_press_time"] = self.tk.call("clock", "milliseconds")
            ui2["valve_btn"].configure(bg="#7a2020")
            ui2["valve_hint"].configure(text="Hold…  2.0 s", fg=AMBER)
            _valve_tick_fn(i)

        def _valve_tick_fn(i):
            ui2 = self._alicat_ui[i]
            if ui2["valve_press_time"] is None:
                return
            now   = self.tk.call("clock", "milliseconds")
            held  = (now - ui2["valve_press_time"]) / 1000.0
            left  = max(0.0, 2.0 - held)
            if left <= 0:
                _valve_fire_fn(i)
                return
            ui2["valve_hint"].configure(text=f"Hold…  {left:.1f} s", fg=AMBER)
            ui2["valve_after_id"] = self.after(50, lambda i2=i: _valve_tick_fn(i2))

        def _valve_release(event, i=idx):
            ui2 = self._alicat_ui[i]
            if ui2["valve_press_time"] is None:
                return
            if ui2["valve_after_id"]:
                self.after_cancel(ui2["valve_after_id"])
                ui2["valve_after_id"] = None
            ui2["valve_press_time"] = None
            ui2["valve_btn"].configure(bg="#3d1e1e")
            ui2["valve_hint"].configure(text="")

        def _valve_fire_fn(i):
            ui2 = self._alicat_ui[i]
            if ui2["valve_after_id"]:
                self.after_cancel(ui2["valve_after_id"])
                ui2["valve_after_id"] = None
            ui2["valve_press_time"] = None
            ui2["valve_btn"].configure(bg="#3d1e1e")
            ui2["valve_hint"].configure(text="✓ Sent SP → 0", fg=GREEN)
            self.after(2000, lambda: ui2["valve_hint"].configure(text=""))
            al = self._alicats[i]
            if al.is_connected:
                try:
                    al.set_setpoint(0.0)
                except Exception as exc:
                    ui2["valve_hint"].configure(text=f"Error: {exc}", fg="#e05555")
            else:
                ui2["valve_hint"].configure(text="Not connected", fg=FG_DIM)

        ui["valve_btn"].bind("<ButtonPress-1>",   _valve_press)
        ui["valve_btn"].bind("<ButtonRelease-1>", _valve_release)

        ui["ctl_status_lbl"] = tk.Label(
            cg, text="Not connected", bg=LFR_BG, fg=FG_DIM, font=FONT_SM, anchor="w")
        ui["ctl_status_lbl"].pack(fill="x", padx=8, pady=(2, 8))

        self._alicat_ui[idx] = ui
        if HAS_SERIAL:
            self._scan_ports(idx)

    # ── File loading ───────────────────────────────────────────────────────

    def _clear_csv(self):
        self._csv_path = None
        self._df       = pd.DataFrame()
        self._file_lbl.configure(text="  no file loaded  ", fg=FG_DIM)
        self._redraw()

    def _open_csv(self):
        path = filedialog.askopenfilename(
            title="Select PicoScope frequency log CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self._csv_path = Path(path)
            self._file_lbl.configure(text=f"  {self._csv_path.name}  ", fg=FG)
            self._reload()

    def _reload(self):
        if self._csv_path is None:
            return
        try:
            self._df = load_picoscope_csv(self._csv_path)
            n    = len(self._df)
            span = self._df["elapsed_s"].iloc[-1] if n else 0.0
            t0   = self._df["timestamp"].iloc[0].strftime("%H:%M:%S") if n else "–"
            t1   = self._df["timestamp"].iloc[-1].strftime("%H:%M:%S") if n else "–"
            self._status(f"Loaded {n:,} rows  ·  {span:.1f} s  ·  {t0} – {t1}")
            self._redraw()
        except Exception as exc:
            messagebox.showerror("Load error", str(exc))

    def _toggle_auto(self):
        if self._auto_var.get():
            self._schedule_refresh()
        else:
            if self._refresh_job:
                self.after_cancel(self._refresh_job)
                self._refresh_job = None

    def _schedule_refresh(self):
        if not self._auto_var.get():
            return
        self._reload()
        try:
            ms = int(float(self._refresh_var.get()) * 1000)
        except ValueError:
            ms = 2000
        self._refresh_job = self.after(ms, self._schedule_refresh)

    # ── Drawing ────────────────────────────────────────────────────────────

    def _get_spin_df(self) -> pd.DataFrame:
        df = self._df.copy()
        if df.empty:
            return df

        # ── Time window ──────────────────────────────────────────────────────
        win = self._window_var.get()
        if win != "All":
            secs = {"30 s": 30, "1 min": 60, "5 min": 300,
                    "10 min": 600, "30 min": 1800}.get(win, None)
            if secs:
                df = df[df["elapsed_s"] >= df["elapsed_s"].iloc[-1] - secs].copy()

        # ── Frequency limits ─────────────────────────────────────────────────
        if self._freq_lim_var.get():
            try:
                lim_unit = self._freq_lim_unit_var.get()
                lim_mult = UNIT_MULTS.get(lim_unit, 1.0)   # lim values are in lim_unit
                lo_hz = float(self._freq_lo_var.get()) / lim_mult
                hi_hz = float(self._freq_hi_var.get()) / lim_mult
                n_before = len(df)
                df = df[(df["frequency_hz"] >= lo_hz) & (df["frequency_hz"] <= hi_hz)].copy()
                n_dropped = n_before - len(df)
                self._freq_lim_count_lbl.configure(
                    text=f"{len(df):,} kept  ·  {n_dropped:,} dropped", fg=FG_DIM)
            except (ValueError, AttributeError):
                pass
        else:
            try:
                self._freq_lim_count_lbl.configure(text="")
            except AttributeError:
                pass

        if df.empty:
            return df

        f = df["frequency_hz"].values.astype(float).copy()

        def _interp_nans(arr: np.ndarray) -> np.ndarray:
            """Linear-interpolate over NaN gaps (needed before scipy filters,
            which otherwise propagate NaN across the whole trace)."""
            nans = np.isnan(arr)
            if nans.any() and (~nans).any():
                xs = np.arange(len(arr))
                arr = arr.copy()
                arr[nans] = np.interp(xs[nans], xs[~nans], arr[~nans])
            return arr

        # ── Despiking ────────────────────────────────────────────────────────
        if self._despike_var.get():
            try:
                thresh = float(self._despike_thresh_var.get())
                # NaN-aware: real PicoScope logs contain NaN entries, which would
                # otherwise make median/MAD NaN and silently disable despiking.
                median = np.nanmedian(f)
                mad    = np.nanmedian(np.abs(f - median)) * 1.4826  # ≈ σ for Gaussian
                if mad > 0:
                    mask = np.abs(f - median) > thresh * mad
                    mask = np.where(np.isnan(mask), False, mask)
                    f[mask] = np.nan
                    f = _interp_nans(f)
            except Exception:
                pass

        # ── Smoothing filter ─────────────────────────────────────────────────
        ftype = self._filter_var.get()
        try:
            n = int(self._fwin_var.get())
        except ValueError:
            n = 25

        if ftype == "Mean" and n > 1:
            f = pd.Series(f).rolling(n, min_periods=1, center=True).mean().values

        elif ftype == "Median" and n > 1:
            f = pd.Series(f).rolling(n, min_periods=1, center=True).median().values

        elif ftype == "Savitzky-Golay" and n > 3:
            if HAS_SCIPY:
                # polyorder must be < window; use 3 or n-1 whichever is smaller
                poly = min(3, n - 1)
                wlen = n if n % 2 == 1 else n + 1   # must be odd
                try:
                    f = savgol_filter(_interp_nans(f), window_length=wlen, polyorder=poly)
                except Exception:
                    pass
            else:
                # fallback: rolling mean
                f = pd.Series(f).rolling(n, min_periods=1, center=True).mean().values

        elif ftype == "Gaussian" and n > 1:
            if HAS_SCIPY:
                sigma = n / 6.0   # n ≈ ±3σ full width
                f = gaussian_filter1d(_interp_nans(f), sigma=sigma)
            else:
                f = pd.Series(f).rolling(n, min_periods=1, center=True).mean().values

        df = df.copy()
        df["frequency_hz"] = f
        return df

    def _merge_for_scatter(self, spin_df: pd.DataFrame, adf: pd.DataFrame) -> pd.DataFrame:
        """Merge Alicat readings onto spin timestamps (nearest, ≤ 2 s tolerance)."""
        if adf.empty or spin_df.empty:
            return pd.DataFrame()
        adf = adf.copy()
        adf["timestamp"] = pd.to_datetime(adf["timestamp"])
        cols = ["timestamp"] + [c for c in ("pressure", "temperature", "vol_flow", "mass_flow")
                                 if c in adf.columns]
        merged = pd.merge_asof(
            spin_df.sort_values("timestamp"),
            adf[cols].sort_values("timestamp"),
            on="timestamp", direction="nearest",
            tolerance=pd.Timedelta("2s"),
        )
        return merged

    def _redraw(self):
        unit = self._unit_var.get()
        mult = UNIT_MULTS[unit]
        spin = self._get_spin_df()
        adf   = self._get_alicat_df(0)
        adf_b = self._get_alicat_df(1)
        has_alic = (not adf.empty) or (not adf_b.empty)
        # Scatter panels use Alicat A; fall back to B if only B is present.
        scat_df = adf if not adf.empty else adf_b

        self._fig.clear()
        self._fig.patch.set_facecolor(BG)

        if spin.empty and not has_alic:
            ax = self._fig.add_subplot(111)
            _style_ax(ax, grid=False)
            ax.text(0.5, 0.5,
                    "Open a PicoScope CSV  or  connect the Alicat to begin",
                    ha="center", va="center", color=FG_DIM,
                    transform=ax.transAxes, fontsize=10)
            ax.set_xticks([]); ax.set_yticks([])
            self._canvas.draw_idle()
            return

        if has_alic and not spin.empty:
            # ── Full layout: 2 time-series left + 2 scatter right ──
            gs = self._fig.add_gridspec(
                2, 2,
                width_ratios=[3, 1.8],
                height_ratios=[1.6, 1],
                hspace=0.06, wspace=0.38,
                left=0.07, right=0.97, top=0.93, bottom=0.10,
            )
            ax_freq = self._fig.add_subplot(gs[0, 0])
            ax_gas  = self._fig.add_subplot(gs[1, 0], sharex=ax_freq)
            ax_sc1  = self._fig.add_subplot(gs[0, 1])
            ax_sc2  = self._fig.add_subplot(gs[1, 1])

            self._draw_freq(ax_freq, spin, unit, mult, xlabel=False)
            self._draw_gas(ax_gas, adf, adf_b)
            merged = self._merge_for_scatter(spin, scat_df)
            self._draw_scatter(ax_sc1, merged, unit, mult, "pressure", C_PRESS, "Pressure")
            self._draw_scatter(ax_sc2, merged, unit, mult, "mass_flow", C_FLOW, "Mass flow")

        elif not spin.empty:
            # ── Spin only ──
            gs = self._fig.add_gridspec(1, 1, left=0.08, right=0.97, top=0.92, bottom=0.10)
            ax_freq = self._fig.add_subplot(gs[0, 0])
            self._draw_freq(ax_freq, spin, unit, mult, xlabel=True)

        else:
            # ── Alicat only (no spin file) ──
            gs = self._fig.add_gridspec(1, 1, left=0.08, right=0.97, top=0.92, bottom=0.10)
            ax_gas = self._fig.add_subplot(gs[0, 0])
            self._draw_gas(ax_gas, adf, adf_b)

        self._canvas.draw_idle()

    # -- individual panel renderers ----------------------------------------

    def _draw_freq(self, ax, spin: pd.DataFrame, unit: str, mult: float, xlabel: bool):
        _style_ax(ax)
        t = spin["timestamp"]
        f = spin["frequency_hz"] * mult
        mean_f, std_f = float(f.mean()), float(f.std())
        rel_sigma = (std_f / mean_f) if mean_f else float("nan")

        if self._show_sigma_var.get():
            ax.fill_between(t, mean_f - std_f, mean_f + std_f,
                            alpha=0.12, color=C_MEAN, zorder=1,
                            label=f"±1σ  {std_f:.4f} {unit}")
        ftype = self._filter_var.get()
        fwin  = self._fwin_var.get()
        flbl  = f"Spin freq ({ftype} N={fwin})" if ftype != "None" else "Spin frequency"
        if self._despike_var.get():
            flbl += f"  [despiked {self._despike_thresh_var.get()}σ]"
        ax.plot(t, f, lw=0.85, color=C_FREQ, zorder=2, label=flbl)
        if self._show_mean_var.get():
            ax.axhline(mean_f, ls="--", lw=0.9, color=C_MEAN, alpha=0.85, zorder=3,
                       label=f"Mean  {mean_f:.4f} {unit}")

        ax.set_ylabel(f"Frequency ({unit})", color=FG)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        _auto_rotate_xlabels(ax)
        if not xlabel:
            ax.set_xticklabels([])
            ax.set_xlabel("")
        else:
            ax.set_xlabel("Time")

        # Stats annotation
        ax.text(0.01, 0.97,
                f"mean {mean_f:.4f} {unit}   σ {std_f:.4f} {unit}   σ/f {rel_sigma:.2e}",
                transform=ax.transAxes, fontsize=6.5, va="top", ha="left", color=FG_DIM)

        leg = ax.legend(loc="upper right", framealpha=0.25,
                        facecolor=BG_PANEL, edgecolor=BORDER, labelcolor=FG)

        parts = []
        if self._csv_path:
            parts.append(self._csv_path.name)
        if self._alicat_file_path and not self._alicats[0].is_connected:
            parts.append(f"+ {self._alicat_file_path.name}")
        ax.set_title("  ·  ".join(parts), fontsize=8, color=FG_DIM, pad=4)

    def _draw_gas(self, ax, adf: pd.DataFrame, adf_b: pd.DataFrame | None = None):
        """Pressure (left axis) and mass-flow (right axis) vs time.

        Plots Alicat A as solid lines and, when connected, Alicat B as dashed
        lines on the same axes so both sensors are visible together.
        """
        _style_ax(ax)

        # (dataframe, suffix, pressure_offset, linestyle)
        units = []
        if adf is not None and not adf.empty:
            units.append((adf, "A", self._alicat_ui[0].get("pressure_offset", LOCAL_ATMOS), "-"))
        if adf_b is not None and not adf_b.empty:
            units.append((adf_b, "B", self._alicat_ui[1].get("pressure_offset", LOCAL_ATMOS), "--"))

        handles = []
        any_pressure = any("pressure" in d.columns for d, *_ in units)
        any_flow     = any("mass_flow" in d.columns for d, *_ in units)
        multi        = len(units) > 1

        ax2 = None
        if any_flow:
            ax2 = ax.twinx()
            _style_ax(ax2, grid=False)

        for d, sfx, poff, ls in units:
            ta = pd.to_datetime(d["timestamp"])
            if "pressure" in d.columns:
                vals = pd.to_numeric(d["pressure"], errors="coerce") - poff
                lbl = f"Pressure {sfx}" if multi else "Pressure"
                l, = ax.plot(ta, vals, lw=0.85, color=C_PRESS, ls=ls, label=lbl)
                handles.append(l)
            if ax2 is not None and "mass_flow" in d.columns:
                vals2 = pd.to_numeric(d["mass_flow"], errors="coerce")
                lbl2 = f"Mass flow {sfx}" if multi else "Mass flow"
                l2, = ax2.plot(ta, vals2, lw=0.85, color=C_FLOW, ls=ls, label=lbl2)
                handles.append(l2)

        if any_pressure:
            ax.set_ylabel("Pressure (barg)", color=C_PRESS)
            ax.tick_params(axis="y", labelcolor=C_PRESS)
        if ax2 is not None:
            ax2.set_ylabel("Mass flow", color=C_FLOW)
            ax2.tick_params(axis="y", labelcolor=C_FLOW)
            ax2.spines["right"].set_edgecolor(C_FLOW)

        ax.set_xlabel("Time")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        _auto_rotate_xlabels(ax)
        ax.spines["left"].set_edgecolor(C_PRESS if any_pressure else BORDER)

        if handles:
            ax.legend(handles=handles, loc="upper right", framealpha=0.25,
                      facecolor=BG_PANEL, edgecolor=BORDER, labelcolor=FG)

    def _draw_scatter(self, ax, merged: pd.DataFrame, unit: str, mult: float,
                      col: str, colour: str, label: str):
        _style_ax(ax)
        ax.set_xlabel(f"Spin freq ({unit})", color=FG)
        ax.set_ylabel(label, color=colour)
        ax.tick_params(axis="y", labelcolor=colour)
        ax.spines["left"].set_edgecolor(colour)

        if merged.empty or col not in merged.columns:
            ax.text(0.5, 0.5, "No Alicat data", ha="center", va="center",
                    color=FG_DIM, transform=ax.transAxes, fontsize=8)
            return

        x = merged["frequency_hz"] * mult
        y = pd.to_numeric(merged[col], errors="coerce")
        if col == "pressure":
            y = y - self._alicat_ui[0].get("pressure_offset", LOCAL_ATMOS)
        mask = x.notna() & y.notna()
        if mask.sum() < 2:
            ax.text(0.5, 0.5, "Insufficient overlap", ha="center", va="center",
                    color=FG_DIM, transform=ax.transAxes, fontsize=8)
            return

        # Colour by time index → shows hysteresis / spin-up path
        idx = np.arange(mask.sum())
        sc  = ax.scatter(x[mask], y[mask], c=idx, cmap="plasma",
                         s=2.5, alpha=0.7, linewidths=0, rasterized=True)
        cb  = self._fig.colorbar(sc, ax=ax, pad=0.02, fraction=0.05)
        cb.set_label("time →", fontsize=6, color=FG_DIM)
        cb.ax.tick_params(labelsize=6, labelcolor=FG_DIM, colors=FG_DIM)
        cb.outline.set_edgecolor(BORDER)

        # Linear trend
        try:
            xv, yv = x[mask].values, y[mask].values
            p  = np.polyfit(xv, yv, 1)
            xf = np.linspace(xv.min(), xv.max(), 200)
            ax.plot(xf, np.polyval(p, xf), lw=1.0, ls="--",
                    color=C_MEAN, alpha=0.7, zorder=5)
        except Exception:
            pass

        ax.set_title(f"Freq vs {label}", fontsize=7.5, color=FG_DIM, pad=3)

    # ── Alicat ─────────────────────────────────────────────────────────────

    def _get_alicat_df(self, idx: int = 0) -> pd.DataFrame:
        """Return the best available dataset for Alicat *idx* (0 = A, 1 = B).

        Priority: live serial data (if connected, even before logging starts)
        → loaded historical file (Alicat A only).
        """
        al = self._alicats[idx]
        if al.is_connected:
            live = al.get_dataframe()
            if not live.empty:
                return live
        # Historical file loading only populates the Alicat A dataset.
        return self._alicat_file_df if idx == 0 else pd.DataFrame()

    def _open_alicat_file(self):
        path = filedialog.askopenfilename(
            title="Open Alicat log CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            df = load_alicat_csv(path)
            self._alicat_file_df   = df
            self._alicat_file_path = Path(path)
            n    = len(df)
            t0   = df["timestamp"].iloc[0].strftime("%H:%M:%S") if n else "–"
            t1   = df["timestamp"].iloc[-1].strftime("%H:%M:%S") if n else "–"
            self._alicat_file_lbl2.configure(
                text=f"  {self._alicat_file_path.name}  ", fg=FG)
            self._status(f"Alicat file loaded: {n:,} rows  ·  {t0} – {t1}")
            self._redraw()
        except Exception as exc:
            messagebox.showerror("Alicat file", str(exc))

    def _clear_alicat_file(self):
        self._alicat_file_df   = pd.DataFrame()
        self._alicat_file_path = None
        self._alicat_file_lbl2.configure(text="  no file loaded  ", fg=FG_DIM)
        self._status("Alicat file cleared.")
        self._redraw()

    def _scan_ports(self, idx: int = 0):
        if not HAS_SERIAL:
            return
        ports = [p.device for p in serial.tools.list_ports.comports()]
        ui = self._alicat_ui[idx]
        if not ui:
            return
        ui["port_cb"]["values"] = ports
        if ports and not ui["port_var"].get():
            ui["port_var"].set(ports[0])

    # ── Unified log ────────────────────────────────────────────────────────

    def _browse_unified_log(self):
        path = filedialog.asksaveasfilename(
            title="Unified log file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="mas_log.csv",
        )
        if path:
            self._unified_csv_path = Path(path)
            self._unified_log_lbl.configure(
                text=f"  {self._unified_csv_path.name}  ", fg=FG)

    def _toggle_unified_log(self):
        if self._unified_logging:
            self._stop_unified_log()
        else:
            if not self._unified_csv_path:
                messagebox.showwarning("Unified log", "Choose a file path first (Browse…).")
                return
            self._start_unified_log(self._unified_csv_path)

    def _start_unified_log(self, path: Path):
        try:
            self._unified_log_fh     = open(path, "w", newline="", encoding="utf-8")
            self._unified_log_writer = csv.writer(self._unified_log_fh)
            self._unified_log_writer.writerow(UNIFIED_CSV_COLS)
            self._unified_log_fh.flush()
            self._unified_log_rows   = 0
            self._unified_logging    = True
            self._unified_log_btn.configure(text="⏹  Stop logging")
            self._unified_log_lbl.configure(
                text=f"  {path.name}  ", fg=GREEN)
        except Exception as exc:
            messagebox.showerror("Unified log", f"Could not open log file:\n{exc}")

    def _stop_unified_log(self):
        self._unified_logging = False
        if self._unified_log_fh is not None:
            try:
                self._unified_log_fh.flush()
                self._unified_log_fh.close()
            except Exception:
                pass
            self._unified_log_fh     = None
            self._unified_log_writer = None
        self._unified_log_btn.configure(text="▶  Start logging")
        if self._unified_csv_path:
            self._unified_log_lbl.configure(
                text=f"  {self._unified_csv_path.name}  ", fg=FG)

    def _write_unified_row(self):
        """Write one row to the unified log: timestamp, latest freq, A readings, B readings."""
        if not self._unified_logging or self._unified_log_writer is None:
            return

        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # Latest frequency — most recent row in loaded PicoScope dataframe
        freq_hz = ""
        if not self._df.empty and "frequency_hz" in self._df.columns:
            last = self._df["frequency_hz"].dropna()
            if not last.empty:
                freq_hz = f"{last.iloc[-1]:.4f}"

        def _alicat_cols(idx: int) -> list:
            al = self._alicats[idx]
            r  = al.last_reading if al.is_connected else None
            if not r:
                return ["", "", "", "", "", ""]
            return [
                r.get("pressure",    ""),
                r.get("temperature", ""),
                r.get("vol_flow",    ""),
                r.get("mass_flow",   ""),
                r.get("setpoint",    ""),
                r.get("gas",         ""),
            ]

        row = [ts, freq_hz] + _alicat_cols(0) + _alicat_cols(1)
        self._unified_log_writer.writerow(row)
        self._unified_log_fh.flush()
        self._unified_log_rows += 1
        self._unified_log_row_lbl.configure(
            text=f"{self._unified_log_rows} rows", fg=FG_DIM)

    # ── Alicat connection ───────────────────────────────────────────────────

    def _toggle_alicat_conn(self, idx: int = 0):
        al = self._alicats[idx]
        ui = self._alicat_ui[idx]
        lname = f"Alicat {'A' if idx == 0 else 'B'}"
        if al.is_connected:
            if idx == 0:
                self._stop_live_redraw()
            al.disconnect()
            if ui.get("conn_btn"):
                ui["conn_btn"].configure(text="Connect")
            if ui.get("alicat_lbl"):
                ui["alicat_lbl"].configure(text=f"{lname}  —  Disconnected.", fg=FG_DIM)
            if ui.get("ctl_status_lbl"):
                ui["ctl_status_lbl"].configure(text="Not connected", fg=FG_DIM)
            for key in ("ctl_p_lbl", "ctl_t_lbl", "ctl_q_lbl", "ctl_sp_lbl"):
                if ui.get(key):
                    ui[key].configure(text="–")
            # Hide Alicat B panel when disconnected
            if idx == 1 and ui.get("panel_frame"):
                ui["panel_frame"].pack_forget()
            self._redraw()
        else:
            port = ui.get("port_var", tk.StringVar()).get() if ui else ""
            if not port:
                messagebox.showwarning(lname, "Select a serial port first.")
                return
            try:
                baud = int(ui["baud_var"].get())
                new_al = AlicatLogger(port, address=ui["addr_var"].get())
                new_al.connect(baud=baud)
                self._alicats[idx] = new_al
                if idx == 0:
                    self._alicat = new_al
                # Reset offset on new connection so Pressure tile shows true gauge
                ui["pressure_offset"] = LOCAL_ATMOS
                if ui.get("zero_lbl"):
                    ui["zero_lbl"].configure(
                        text=f"offset: {ui['pressure_offset']:.5f} bar", fg=FG_DIM)
                if ui.get("conn_btn"):
                    ui["conn_btn"].configure(text="Disconnect")
                if ui.get("alicat_lbl"):
                    ui["alicat_lbl"].configure(text=f"{lname}  —  Connected: {port}", fg=GREEN)
                if ui.get("ctl_status_lbl"):
                    ui["ctl_status_lbl"].configure(
                        text=f"Connected  ·  {port}", fg=GREEN)
                # Show Alicat B panel when connected
                if idx == 1 and ui.get("panel_frame"):
                    ui["panel_frame"].pack(side="left", padx=(0, 10), fill="y", pady=2)
                if idx == 0:
                    self._start_live_redraw()
            except Exception as exc:
                messagebox.showerror(lname, str(exc))

    def _poll_alicat_status(self):
        for idx in range(2):
            al = self._alicats[idx]
            ui = self._alicat_ui[idx]
            if not ui:
                continue
            lname = f"Alicat {'A' if idx == 0 else 'B'}"
            if al.is_connected:
                r = al.last_reading
                if r:
                    parts = []
                    for k, lbl in [("pressure", "P"), ("temperature", "T"), ("mass_flow", "Q")]:
                        if k in r:
                            try:
                                parts.append(f"{lbl}: {float(r[k]):.2f}")
                            except (ValueError, TypeError):
                                pass
                    if parts and ui.get("alicat_lbl"):
                        ui["alicat_lbl"].configure(
                            text=f"{lname}  —  " + "   ".join(parts), fg=GREEN)
                    self._update_control_readout(r, idx)

                if al.error:
                    err_msg = f"Error: {al.error}"
                    if ui.get("alicat_lbl"):
                        ui["alicat_lbl"].configure(text=f"{lname}  —  {err_msg}", fg=RED)
                    if ui.get("ctl_status_lbl"):
                        raw_preview = al.last_raw[:50] if al.last_raw else ""
                        ui["ctl_status_lbl"].configure(
                            text=f"{err_msg}  ·  last raw: {raw_preview!r}", fg=RED)
                elif not al.last_reading and ui.get("ctl_status_lbl"):
                    raw_preview = al.last_raw[:60] if al.last_raw else "waiting for response…"
                    n_readings = len(al._data)
                    ui["ctl_status_lbl"].configure(
                        text=f"{n_readings} readings  ·  raw: {raw_preview!r}", fg=AMBER)

        self._write_unified_row()
        self.after(1000, self._poll_alicat_status)

    def _update_control_readout(self, r: dict, idx: int = 0):
        """Push latest Alicat reading into the control-panel tiles for given index."""
        ui = self._alicat_ui[idx]
        if not ui or not ui.get("ctl_p_lbl"):
            return
        al = self._alicats[idx]
        offset = ui.get("pressure_offset", LOCAL_ATMOS)

        def _fmt(key, decimals=3):
            try:
                v = float(r[key])
                if key == "pressure":
                    # Device reports absolute; subtract offset (default=LOCAL_ATMOS)
                    # so the tile always shows gauge (barg).
                    v -= offset
                elif key == "setpoint":
                    # Setpoint is also stored absolute on the device.
                    # Show 0 when valve is closed (≤0 abs), otherwise convert to gauge.
                    v = 0.0 if v <= 0.0 else v - LOCAL_ATMOS
                return f"{v:.{decimals}f}"
            except (KeyError, ValueError, TypeError):
                return "–"

        ui["ctl_p_lbl"].configure(text=_fmt("pressure", 4))
        ui["ctl_t_lbl"].configure(text=_fmt("temperature", 2))
        ui["ctl_q_lbl"].configure(text=_fmt("mass_flow", 4))
        ui["ctl_sp_lbl"].configure(text=_fmt("setpoint", 4))

        n_readings = len(al._data)
        raw_preview = al.last_raw[:60] if al.last_raw else "waiting…"
        ui["ctl_status_lbl"].configure(
            text=f"{n_readings} readings  ·  {raw_preview}", fg=GREEN)

    def _open_serial_monitor(self, idx: int = 0):
        """Open a live scrolling window showing raw TX/RX bytes."""
        al = self._alicats[idx]
        lname = f"Alicat {'A' if idx == 0 else 'B'}"
        win = tk.Toplevel(self)
        win.title(f"Serial monitor  —  {lname}")
        win.configure(bg=BG)
        win.geometry("700x400")

        hdr = tk.Frame(win, bg=BG_PANEL, pady=4, padx=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"{lname}  raw serial log  (TX → device, RX ← device)",
                 bg=BG_PANEL, fg=FG_DIM, font=FONT_SM, anchor="w").pack(side="left")

        # Manual TX entry
        tx_var = tk.StringVar(value="A\r")
        tx_entry = _entry(hdr, tx_var, width=12)
        tx_entry.pack(side="right", padx=(4, 0))
        tk.Label(hdr, text="Send:", bg=BG_PANEL, fg=FG, font=FONT_SM).pack(side="right")

        txt = tk.Text(win, bg=BG_ENTRY, fg=FG, font=("Menlo", 10),
                      relief="flat", bd=0, wrap="none",
                      insertbackground=FG,
                      highlightbackground=BORDER, highlightthickness=1)
        txt.pack(fill="both", expand=True, padx=6, pady=6)

        sb = ttk.Scrollbar(win, command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.configure(yscrollcommand=sb.set)

        # Colour tags
        txt.tag_configure("ts",  foreground=FG_DIM)
        txt.tag_configure("tx",  foreground=ACCENT)
        txt.tag_configure("rx",  foreground=GREEN)
        txt.tag_configure("err", foreground=RED)
        txt.tag_configure("hex", foreground=AMBER)

        def _manual_send():
            if not al.is_connected:
                return
            raw = tx_var.get().replace("\\r", "\r").replace("\\n", "\n")
            try:
                c = al._conn
                c.reset_input_buffer()
                c.rts = True
                c.write(raw.encode())
                c.flush()
                time.sleep(0.015)
                c.rts = False
                time.sleep(0.05)
                resp = c.read_until(b"\r")
                decoded = resp.decode("ascii", errors="replace")
                ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                txt.insert("end", f"{ts}  ", "ts")
                txt.insert("end", f"TX {raw!r}  ", "tx")
                txt.insert("end", f"RX({len(resp)}B): {decoded!r}\n", "rx" if decoded.strip() else "hex")
                txt.see("end")
            except Exception as exc:
                txt.insert("end", f"Error: {exc}\n", "err")

        _btn(hdr, "Send", _manual_send).pack(side="right", padx=(0, 6))
        tx_entry.bind("<Return>", lambda _: _manual_send())

        last_len = [0]

        def _refresh():
            if not win.winfo_exists():
                return
            log = list(al._raw_log)
            if len(log) != last_len[0]:
                txt.delete("1.0", "end")
                for line in log:
                    # colour TX part blue, RX part green
                    if "RX" in line and "(hex:" in line:
                        txt.insert("end", line + "\n", "hex")
                    elif "RX" in line:
                        txt.insert("end", line + "\n", "rx")
                    else:
                        txt.insert("end", line + "\n", "ts")
                txt.see("end")
                last_len[0] = len(log)
            win.after(500, _refresh)

        _refresh()

    # ── Spin routines ──────────────────────────────────────────────────────

    # ── Spin routines ──────────────────────────────────────────────────────

    def _open_routine_editor(self):
        """Toplevel window for editing spin-up and spin-down step sequences."""
        win = tk.Toplevel(self)
        win.title("Edit spin routines")
        win.configure(bg=BG)
        win.geometry("700x480")
        win.resizable(True, True)

        tk.Label(win, text="Define step sequences  (setpoint → hold for duration, then next step)",
                 bg=BG, fg=FG_DIM, font=FONT_SM, anchor="w").pack(fill="x", padx=12, pady=(8, 4))

        pane = tk.Frame(win, bg=BG)
        pane.pack(fill="both", expand=True, padx=8, pady=4)
        pane.columnconfigure(0, weight=1)
        pane.columnconfigure(1, weight=1)

        def _make_table(parent, title, steps_ref, colour):
            """Build an editable step table inside *parent*."""
            grp = _group(parent, title)
            grp.grid(sticky="nsew", padx=4, pady=2)

            hdr = tk.Frame(grp, bg=LFR_BG)
            hdr.pack(fill="x", padx=8, pady=(4, 0))
            for txt, w in [("Step", 4), ("Setpoint (barg)", 14), ("Duration (s)", 12)]:
                tk.Label(hdr, text=txt, bg=LFR_BG, fg=FG_DIM,
                         font=FONT_SM, anchor="w", width=w).pack(side="left", padx=(0, 4))

            # Scrollable rows
            canvas = tk.Canvas(grp, bg=LFR_BG, highlightthickness=0, height=240)
            vsb = ttk.Scrollbar(grp, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y", padx=(0, 4))
            canvas.pack(fill="both", expand=True, padx=8)

            inner = tk.Frame(canvas, bg=LFR_BG)
            canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>",
                       lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>",
                        lambda e: canvas.itemconfig(canvas_win, width=e.width))

            row_vars = []   # list of (sp_var, dur_var)

            def _render():
                for w in inner.winfo_children():
                    w.destroy()
                row_vars.clear()
                for i, (sp, dur) in enumerate(steps_ref):
                    r = tk.Frame(inner, bg=LFR_BG)
                    r.pack(fill="x", pady=1)
                    tk.Label(r, text=f"{i+1}.", bg=LFR_BG, fg=FG_DIM,
                             font=FONT_SM, width=3, anchor="e").pack(side="left")
                    sv = tk.StringVar(value=f"{sp:.4f}")
                    dv = tk.StringVar(value=f"{dur:.1f}")
                    _entry(r, sv, width=12).pack(side="left", padx=(4, 4))
                    _entry(r, dv, width=10).pack(side="left", padx=(0, 4))
                    row_vars.append((sv, dv))
                    idx = i
                    _btn(r, "✕", lambda i=idx: _del(i), width=2,
                         bg="#3d1e1e").pack(side="left")

            def _del(i):
                steps_ref.pop(i)
                _render()

            def _add():
                last_sp = steps_ref[-1][0] if steps_ref else 0.0
                steps_ref.append((last_sp, 10.0))
                _render()
                canvas.yview_moveto(1.0)

            def _save():
                steps_ref.clear()
                for sv, dv in row_vars:
                    try:
                        sp  = float(sv.get())
                        dur = float(dv.get())
                        steps_ref.append((sp, dur))
                    except ValueError:
                        pass
                total = sum(d for _, d in steps_ref)
                status = (f"{len(steps_ref)} steps  ·  "
                          f"{total:.0f} s total  ({total/60:.1f} min)")
                info_lbl.configure(text=status, fg=GREEN)

            _render()

            foot = tk.Frame(grp, bg=LFR_BG)
            foot.pack(fill="x", padx=8, pady=(4, 2))
            _btn(foot, "+ Add step", _add).pack(side="left", padx=(0, 6))
            _btn(foot, "✓ Save", _save, bg="#1e3a1e").pack(side="left")

            info_lbl = tk.Label(grp, text="", bg=LFR_BG, fg=FG_DIM,
                                font=FONT_SM, anchor="w")
            info_lbl.pack(fill="x", padx=8, pady=(0, 6))

            return grp

        _make_table(pane, "↑ Spin-up sequence",   self._spinup_steps,   C_FLOW).grid(
            row=0, column=0, sticky="nsew", padx=4, pady=2)
        _make_table(pane, "↓ Spin-down sequence", self._spindown_steps, C_PRESS).grid(
            row=0, column=1, sticky="nsew", padx=4, pady=2)
        pane.rowconfigure(0, weight=1)

        tk.Label(win, text="Click  ✓ Save  in each panel to apply before running.",
                 bg=BG, fg=FG_DIM, font=FONT_SM, anchor="w").pack(fill="x", padx=12, pady=(2, 8))

    @staticmethod
    def _gauge_to_abs(gauge: float) -> float:
        """Map a typed setpoint to the value sent to the device.

        The device reports and accepts **absolute pressure**. The user types gauge
        (barg); we add LOCAL_ATMOS to get the absolute value to send. SP <= 0 sends
        0.0 absolute so the valve fully closes."""
        return 0.0 if gauge <= 0.0 else gauge + LOCAL_ATMOS

    def _routine_running(self) -> bool:
        return (self._routine_thread is not None
                and self._routine_thread.is_alive())

    def _start_spinup(self):
        if not self._alicat.is_connected:
            messagebox.showwarning("Spin routine", "Connect the Alicat first.")
            return
        if not self._spinup_steps:
            messagebox.showwarning("Spin routine",
                                   "No spin-up steps defined.\nClick 'Edit routines…' to add steps.")
            return
        self._launch_routine(self._spinup_steps, label="Spin UP")

    def _start_spindown(self):
        if not self._alicat.is_connected:
            messagebox.showwarning("Spin routine", "Connect the Alicat first.")
            return
        if not self._spindown_steps:
            messagebox.showwarning("Spin routine",
                                   "No spin-down steps defined.\nClick 'Edit routines…' to add steps.")
            return
        self._launch_routine(self._spindown_steps, label="Spin DOWN")

    def _launch_routine(self, steps: list[tuple[float, float]], label: str):
        if self._routine_running():
            if not messagebox.askyesno("Spin routine",
                                       "A routine is already running.\nStop it and start the new one?"):
                return
            self._stop_routine()
            time.sleep(0.15)

        self._routine_stop.clear()
        self._routine_pause.clear()
        self._routine_thread = threading.Thread(
            target=self._run_routine,
            args=(steps, label),
            daemon=True,
        )
        self._routine_thread.start()
        self._routine_lbl.configure(fg=GREEN)
        self._poll_routine_status()

    def _run_routine(self, steps: list[tuple[float, float]], label: str):
        """Background thread: step through (setpoint, duration) pairs with optional ramping."""
        try:
            ramp = float(self._ramp_rate_var.get())
        except ValueError:
            ramp = 0.0
        ramp = max(0.0, ramp)

        TICK = 0.25  # seconds between ramp ticks

        def _wait_or_abort(dur: float, status_fn) -> bool:
            """Wait *dur* seconds, honouring pause/stop. Returns True if aborted."""
            elapsed = 0.0
            while elapsed < dur:
                if self._routine_stop.is_set():
                    return True
                if self._routine_pause.is_set():
                    self._routine_status = status_fn() + "  [PAUSED]"
                    while self._routine_pause.is_set():
                        if self._routine_stop.is_set():
                            return True
                        time.sleep(0.1)
                time.sleep(TICK)
                elapsed += TICK
            return False

        def _ramp_to(sp_from: float, sp_to: float, step_label: str) -> bool:
            """Ramp from sp_from → sp_to. Returns True if aborted."""
            if ramp <= 0.0 or abs(sp_to - sp_from) < 1e-6:
                try:
                    self._alicat.set_setpoint(sp_to)
                except Exception as e:
                    self._routine_status = f"Error: {e}"
                    return True
                return False
            direction = 1.0 if sp_to > sp_from else -1.0
            current = sp_from
            while direction * (sp_to - current) > 1e-6:
                if self._routine_stop.is_set():
                    return True
                if self._routine_pause.is_set():
                    self._routine_status = step_label + f"  ramping…  SP {current:.4f}  [PAUSED]"
                    while self._routine_pause.is_set():
                        if self._routine_stop.is_set():
                            return True
                        time.sleep(0.1)
                step_size = ramp * TICK
                current = current + direction * step_size
                # Clamp to target
                if direction * (current - sp_to) > 0:
                    current = sp_to
                self._routine_status = step_label + f"  ramping…  SP {current:.4f}"
                try:
                    self._alicat.set_setpoint(current)
                except Exception as e:
                    self._routine_status = f"Error: {e}"
                    return True
                time.sleep(TICK)
            return False

        n = len(steps)
        # Step setpoints are entered as gauge (barg); the device works in absolute.
        # Seed prev from the device's last (absolute) setpoint reading for a
        # smooth initial ramp; otherwise start from the first step.
        prev_abs = self._gauge_to_abs(steps[0][0]) if steps else 0.0
        if self._alicat and self._alicat.last_reading:
            try:
                prev_abs = float(self._alicat.last_reading.get("setpoint", prev_abs))
            except (TypeError, ValueError):
                pass

        for i, (sp_gauge, dur) in enumerate(steps):
            if self._routine_stop.is_set():
                break
            target_abs = self._gauge_to_abs(sp_gauge)
            step_lbl = f"{label}  step {i+1}/{n}  →  SP {sp_gauge:.4f} barg"
            self._routine_status = step_lbl + "  (ramping…)"

            # Ramp to target setpoint (absolute values for the device)
            if _ramp_to(prev_abs, target_abs, step_lbl):
                self._routine_status = f"{label}  stopped at step {i+1}/{n}"
                return
            prev_abs = target_abs

            # Hold at setpoint for dur seconds
            self._routine_status = f"{step_lbl}  ({dur:.0f} s hold)"
            if _wait_or_abort(dur, lambda s=step_lbl, d=dur: f"{s}  ({d:.0f} s hold)"):
                self._routine_status = f"{label}  stopped at step {i+1}/{n}"
                return

        if not self._routine_stop.is_set():
            self._routine_status = f"{label}  complete  ✓"

    def _pause_routine(self):
        if not self._routine_running():
            return
        if self._routine_pause.is_set():
            self._routine_pause.clear()          # resume
            self._pause_btn.configure(text="⏸ Pause")
        else:
            self._routine_pause.set()            # pause
            self._pause_btn.configure(text="▶ Resume")

    def _stop_routine(self):
        self._routine_stop.set()
        self._routine_pause.clear()
        self._pause_btn.configure(text="⏸ Pause")
        if hasattr(self, "_routine_lbl"):
            self._routine_lbl.configure(text="Stopped", fg=RED)

    def _poll_routine_status(self):
        """Keep the routine status label updated from the UI thread."""
        if not self._routine_running():
            # Thread just finished
            final = self._routine_status or "No routine running"
            col = GREEN if "✓" in final else (RED if "stop" in final.lower() else FG_DIM)
            self._routine_lbl.configure(text=final, fg=col)
            self._pause_btn.configure(text="⏸ Pause")
            return
        self._routine_lbl.configure(text=self._routine_status)
        self.after(250, self._poll_routine_status)

    def _clear_pressure_offset(self, idx: int = 0):
        """Reset the display offset to LOCAL_ATMOS so the Pressure tile shows
        true gauge (device absolute − local atmosphere = barg)."""
        ui = self._alicat_ui[idx]
        ui["pressure_offset"] = LOCAL_ATMOS
        ui["zero_lbl"].configure(
            text=f"offset: {LOCAL_ATMOS:.5f} bar", fg=FG_DIM)
        self._redraw()

    def _set_zero_from_reading(self, idx: int = 0):
        """Capture the current live pressure reading as the gauge zero.
        After this the display reads 0 at the current applied pressure."""
        ui = self._alicat_ui[idx]
        al = self._alicats[idx]
        r  = al.last_reading if al.is_connected else None
        if not r:
            messagebox.showwarning(
                f"Alicat {'A' if idx == 0 else 'B'}",
                "No live reading available — connect the Alicat first.")
            return
        try:
            raw = float(r["pressure"])
        except (KeyError, ValueError, TypeError):
            messagebox.showwarning(
                f"Alicat {'A' if idx == 0 else 'B'}",
                "Could not read current pressure.")
            return
        ui["pressure_offset"] = raw
        ui["zero_lbl"].configure(
            text=f"offset: {raw:.5f} bar  (zeroed at reading)", fg=GREEN)
        self._redraw()

    def _send_setpoint(self, idx: int = 0):
        al  = self._alicats[idx]
        ui  = self._alicat_ui[idx]
        if not al.is_connected:
            messagebox.showwarning("Alicat", "Not connected.")
            return
        try:
            # User enters gauge (barg). Device works in absolute, so we add
            # LOCAL_ATMOS before sending. SP = 0 → 0 absolute (valve closed).
            target_gauge = float(ui["sp_entry_var"].get())
            target = self._gauge_to_abs(target_gauge)
        except ValueError:
            messagebox.showerror("Setpoint", "Enter a valid number.")
            return
        try:
            ramp = max(0.0, float(ui["sp_ramp_var"].get()))
        except ValueError:
            ramp = 0.0

        # Cancel any in-progress ramp for this unit
        thr = ui.get("sp_ramp_thread")
        stp = ui["sp_ramp_stop"]
        if thr and thr.is_alive():
            stp.set()
            thr.join(timeout=1.0)
        stp.clear()

        status_lbl = ui["ctl_status_lbl"]

        if ramp <= 0.0:
            try:
                al.set_setpoint(target)
                status_lbl.configure(text=f"Setpoint → {target_gauge:.4f} barg sent", fg=ACCENT)
            except Exception as exc:
                messagebox.showerror("Setpoint", str(exc))
        else:
            def _do_ramp(al=al, target=target, ramp=ramp, stp=stp, status_lbl=status_lbl):
                current = target
                if al.last_reading:
                    try:
                        current = float(al.last_reading.get("setpoint", target))
                    except (TypeError, ValueError):
                        pass
                direction = 1.0 if target > current else -1.0
                if abs(target - current) < 1e-6:
                    return
                TICK = 0.25
                step = ramp * TICK
                self.after(0, lambda: status_lbl.configure(
                    text=f"Ramping → {target_gauge:.4f} barg  ({ramp:.4f}/s) …", fg=AMBER))
                while direction * (target - current) > 1e-6:
                    if stp.is_set():
                        self.after(0, lambda: status_lbl.configure(
                            text="Ramp cancelled", fg=FG_DIM))
                        return
                    current = min(target, current + direction * step) if direction > 0 \
                              else max(target, current + direction * step)
                    try:
                        al.set_setpoint(current)
                    except Exception as exc:
                        self.after(0, lambda e=exc: status_lbl.configure(
                            text=f"Ramp error: {e}", fg="#e05555"))
                        return
                    v = current
                    self.after(0, lambda v=v: status_lbl.configure(
                        text=f"Ramping… SP {v:.4f}", fg=AMBER))
                    time.sleep(TICK)
                self.after(0, lambda: status_lbl.configure(
                    text=f"Setpoint → {target_gauge:.4f} barg reached", fg=ACCENT))

            t = threading.Thread(target=_do_ramp, daemon=True)
            ui["sp_ramp_thread"] = t
            t.start()

    _GAS_IDS = {
        "Air":  0,
        "Ar":   1,
        "CH4":  2,
        "CO":   3,
        "CO2":  4,
        "C2H6": 5,
        "H2":   6,
        "He":   7,
        "N2":   8,
    }

    def _send_gas(self, idx: int = 0):
        al = self._alicats[idx]
        ui = self._alicat_ui[idx]
        if not al.is_connected:
            return
        gas = ui["gas_var"].get()
        gid = self._GAS_IDS.get(gas, 0)
        try:
            al.set_gas(gid)
            ui["ctl_status_lbl"].configure(text=f"Gas → {gas} sent", fg=ACCENT)
        except Exception as exc:
            messagebox.showerror("Gas", str(exc))

    def _start_live_redraw(self):
        """Begin periodic plot refresh driven by live Alicat data."""
        if self._live_redraw_job is not None:
            return  # already running
        self._live_redraw_tick()

    def _stop_live_redraw(self):
        if self._live_redraw_job is not None:
            self.after_cancel(self._live_redraw_job)
            self._live_redraw_job = None

    def _live_redraw_tick(self):
        if self._alicat.is_connected:
            # Only redraw from the live timer if PicoScope auto-refresh is off
            # (to avoid double-drawing when both are active).
            if not self._auto_var.get():
                self._redraw()
            self._live_redraw_job = self.after(1000, self._live_redraw_tick)
        else:
            self._live_redraw_job = None

    # ── Export ─────────────────────────────────────────────────────────────

    def _use_visible_range(self):
        try:
            ax  = self._fig.axes[0]
            lo, hi = ax.get_xlim()
            fmt = "%Y-%m-%d %H:%M:%S"
            self._t_from.delete(0, "end")
            self._t_from.insert(0, mdates.num2date(lo).strftime(fmt))
            self._t_to.delete(0, "end")
            self._t_to.insert(0, mdates.num2date(hi).strftime(fmt))
        except Exception:
            pass

    def _clipped(self):
        spin  = self._get_spin_df()
        alic  = self._get_alicat_df(0)
        alic_b = self._get_alicat_df(1)
        t0_s = self._t_from.get().strip()
        t1_s = self._t_to.get().strip()

        def _clip(df, t, lower: bool):
            if df.empty:
                return df
            ts = pd.to_datetime(df["timestamp"])
            return df[ts >= t] if lower else df[ts <= t]

        try:
            if t0_s:
                t0 = pd.Timestamp(t0_s)
                spin   = _clip(spin,   t0, lower=True)
                alic   = _clip(alic,   t0, lower=True)
                alic_b = _clip(alic_b, t0, lower=True)
            if t1_s:
                t1 = pd.Timestamp(t1_s)
                spin   = _clip(spin,   t1, lower=False)
                alic   = _clip(alic,   t1, lower=False)
                alic_b = _clip(alic_b, t1, lower=False)
        except Exception as exc:
            messagebox.showerror("Time range", str(exc))
        return spin, alic, alic_b

    def _export_plot(self, fmt: str):
        spin, alic, alic_b = self._clipped()
        if spin.empty:
            messagebox.showwarning("Export", "No data in selected range.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            filetypes=[(fmt.upper(), f"*.{fmt}"), ("All files", "*.*")],
            initialfile=f"mas_plot.{fmt}",
        )
        if not path:
            return

        import matplotlib.pyplot as plt
        unit = self._unit_var.get()
        mult = UNIT_MULTS[unit]
        has_alic = (not alic.empty) or (not alic_b.empty)
        # Scatter panels use Alicat A; fall back to B if only B is present.
        scat_df = alic if not alic.empty else alic_b

        if has_alic:
            fig = plt.figure(figsize=(10, 6), facecolor="white")
            gs  = fig.add_gridspec(2, 2, width_ratios=[3, 1.8],
                                   height_ratios=[1.6, 1],
                                   hspace=0.08, wspace=0.38,
                                   left=0.07, right=0.97, top=0.93, bottom=0.10)
            ax_f  = fig.add_subplot(gs[0, 0])
            ax_g  = fig.add_subplot(gs[1, 0], sharex=ax_f)
            ax_s1 = fig.add_subplot(gs[0, 1])
            ax_s2 = fig.add_subplot(gs[1, 1])
        else:
            fig, ax_f = plt.subplots(1, 1, figsize=(7, 3.5),
                                     facecolor="white",
                                     constrained_layout=True)
            ax_g = ax_s1 = ax_s2 = None

        # -- publication light style --
        for ax in fig.get_axes():
            ax.set_facecolor("white")
            ax.grid(True, lw=0.3, alpha=0.5)

        t, f = spin["timestamp"], spin["frequency_hz"] * mult
        mean_f, std_f = float(f.mean()), float(f.std())
        rel_sigma = (std_f / mean_f) if mean_f else float("nan")

        if self._show_sigma_var.get():
            ax_f.fill_between(t, mean_f - std_f, mean_f + std_f,
                              alpha=0.10, color=C_MEAN,
                              label=f"±1σ  {std_f:.4f} {unit}")
        ax_f.plot(t, f, lw=0.8, color=C_FREQ, label="Spin frequency")
        if self._show_mean_var.get():
            ax_f.axhline(mean_f, ls="--", lw=0.9, color=C_MEAN,
                         label=f"Mean  {mean_f:.4f} {unit}")
        ax_f.set_ylabel(f"Frequency ({unit})")
        ax_f.legend(fontsize=7)
        ax_f.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        title_parts = []
        if self._csv_path:
            title_parts.append(self._csv_path.name)
        if self._alicat_file_path and not self._alicats[0].is_connected:
            title_parts.append(self._alicat_file_path.name)
        ax_f.set_title(
            "MAS spin frequency  —  " + "  ·  ".join(title_parts) if title_parts
            else "MAS spin frequency",
            fontsize=9,
        )
        ax_f.text(0.01, 0.02,
                  f"n={len(f):,}  mean={mean_f:.4f} {unit}  σ={std_f:.4f} {unit}  σ/f={rel_sigma:.2e}",
                  transform=ax_f.transAxes, fontsize=6, va="bottom",
                  bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.8, ec="lightgrey"))
        if ax_g is None:
            ax_f.set_xlabel("Time")
            fig.autofmt_xdate(rotation=20)
        else:
            ax_f.set_xticklabels([])

        if ax_g is not None:
            units = []
            if not alic.empty:
                units.append((alic, "A", self._alicat_ui[0].get("pressure_offset", LOCAL_ATMOS), "-"))
            if not alic_b.empty:
                units.append((alic_b, "B", self._alicat_ui[1].get("pressure_offset", LOCAL_ATMOS), "--"))
            multi = len(units) > 1
            any_pressure = any("pressure" in d.columns for d, *_ in units)
            any_flow     = any("mass_flow" in d.columns for d, *_ in units)
            handles = []
            axr = ax_g.twinx() if any_flow else None
            for d, sfx, poff, ls in units:
                ta = pd.to_datetime(d["timestamp"])
                if "pressure" in d.columns:
                    lbl = f"Pressure {sfx}" if multi else "Pressure"
                    h, = ax_g.plot(ta,
                              pd.to_numeric(d["pressure"], errors="coerce") - poff,
                              lw=0.8, color=C_PRESS, ls=ls, label=lbl)
                    handles.append(h)
                if axr is not None and "mass_flow" in d.columns:
                    lbl2 = f"Mass flow {sfx}" if multi else "Mass flow"
                    h2, = axr.plot(ta, pd.to_numeric(d["mass_flow"], errors="coerce"),
                             lw=0.8, color=C_FLOW, ls=ls, label=lbl2)
                    handles.append(h2)
            if any_pressure:
                ax_g.set_ylabel("Pressure (barg)", color=C_PRESS)
            if axr is not None:
                axr.set_ylabel("Mass flow", color=C_FLOW)
            ax_g.set_xlabel("Time")
            ax_g.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            if handles:
                ax_g.legend(handles=handles, fontsize=7)
            fig.autofmt_xdate(rotation=20)

            merged = self._merge_for_scatter(spin, scat_df)
            for ax_sc, col, colour, lbl in [
                (ax_s1, "pressure",  C_PRESS, "Pressure"),
                (ax_s2, "mass_flow", C_FLOW,  "Mass flow"),
            ]:
                ax_sc.set_xlabel(f"Spin freq ({unit})", fontsize=8)
                ax_sc.set_ylabel(lbl, color=colour, fontsize=8)
                if not merged.empty and col in merged.columns:
                    x = merged["frequency_hz"] * mult
                    y = pd.to_numeric(merged[col], errors="coerce")
                    if col == "pressure":
                        y = y - self._alicat_ui[0].get("pressure_offset", LOCAL_ATMOS)
                    mask = x.notna() & y.notna()
                    if mask.sum() >= 2:
                        idx = np.arange(mask.sum())
                        ax_sc.scatter(x[mask], y[mask], c=idx, cmap="plasma",
                                      s=2, alpha=0.7, linewidths=0, rasterized=True)
                        xv, yv = x[mask].values, y[mask].values
                        p  = np.polyfit(xv, yv, 1)
                        xf = np.linspace(xv.min(), xv.max(), 200)
                        ax_sc.plot(xf, np.polyval(p, xf), lw=1.0, ls="--",
                                   color=C_MEAN, alpha=0.7)
                ax_sc.set_title(f"Freq vs {lbl}", fontsize=8)

        fig.savefig(path, dpi=300)
        plt.close(fig)
        self._status(f"Plot saved → {Path(path).name}")

    def _export_csv(self):
        spin, alic, _alic_b = self._clipped()
        if spin.empty:
            messagebox.showwarning("Export", "No data in selected range.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="mas_data.csv",
        )
        if not path:
            return
        unit = self._unit_var.get()
        out  = spin[["timestamp", "elapsed_s", "frequency_hz"]].copy()
        out[f"frequency_{unit}"] = out["frequency_hz"] * UNIT_MULTS[unit]
        if not alic.empty:
            alic = alic.copy()
            alic["timestamp"] = pd.to_datetime(alic["timestamp"])
            out = pd.merge_asof(
                out.sort_values("timestamp"),
                alic.sort_values("timestamp"),
                on="timestamp", direction="nearest",
                tolerance=pd.Timedelta("2s"),
            )
        out.to_csv(path, index=False)
        self._status(f"Data saved → {Path(path).name}  ({len(out):,} rows)")

    # ── Helpers ────────────────────────────────────────────────────────────

    def _status(self, msg: str):
        self._status_var.set(msg)
        self.update_idletasks()


# ── Utility ────────────────────────────────────────────────────────────────

def _auto_rotate_xlabels(ax, rotation=25):
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(rotation)
        lbl.set_ha("right")


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = MASMonitor()
    app.mainloop()
