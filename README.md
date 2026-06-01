# MAS Rotor Monitor

Live dashboard for optically-detected MAS NMR rotors.

Reads the frequency-log CSV from PicoScope's built-in datalogging, overlays live
Alicat flow/pressure data from up to **two** Alicat meters (A + B), and lets you
control their setpoints — all in one dark-themed GUI.

The plot fills the top of the window; the controls below are organised into tabs:

| Tab | Purpose |
|---|---|
| **Frequency** | Load spin CSV, display options, filters, frequency limits |
| **Gas & Control** | Connect Alicats, pressure setpoint / gas / valve control |
| **Logging** | Unified CSV logging and historical-file loading |
| **Export** | Save plots (PDF/PNG) and merged data (CSV) |
| **Routines** | Pre-programmed spin-up / spin-down step sequences |

---

## Quick start

**1. Create the venv (once)**

Requires Python 3.13+ for Tk 9 (use Homebrew on macOS):

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install matplotlib pandas numpy pyserial pyftdi
.venv/bin/pip install scipy          # optional — enables Savitzky-Golay / Gaussian filters
```

**2. Run**

```bash
.venv/bin/python scripts/mas_monitor.py
```

---

## Workflow

### Step 1 — Load spin frequency data

#### Option A — PicoScope live logging

In PicoScope 6 or 7:
- Add a *Frequency* measurement on channel A
- Go to *File → Save As → Data Logging* and start logging

PicoScope writes a semicolon-delimited CSV:

```
Time (UTC +02:00 yyyy-MM-dd HH:mm:ss);Frequency (A) (Hz)
2024-01-15 13:11:21;63493,363
```

Click **Open…** in the Frequency tab to load it. Tick **Auto** + set a refresh
interval (e.g. 2 s) to keep the view live as PicoScope appends new rows.

#### Option B — Load old files

- **Spin data**: click **Open…** in the *Frequency* tab to load any historical PicoScope CSV
- **Unified log**: click **Load file** in the *Logging* tab to load a previously-saved
  unified log CSV (see §Logging below)

---

### Step 2 — Connect Alicats (optional)

Two Alicat meters can be connected independently (Alicat A and Alicat B).

1. Select the serial port, baud rate (usually 19200), and device address (default `A` / `B`)
2. Click **Connect**

The Alicat starts streaming immediately once connected. Alicat B's control panel
appears automatically when it is connected.

The 4-panel view activates automatically when Alicat data is available:

```
┌─────────────────────────┬──────────────────┐
│  Spin frequency vs time │ Freq vs pressure  │
│  (mean + ±1σ band)      │  scatter          │
├─────────────────────────┼──────────────────┤
│  Pressure + flow vs time│ Freq vs flow      │
│  (shared time axis)     │  scatter          │
└─────────────────────────┴──────────────────┘
```

If only an Alicat is connected (no spin file loaded), a single pressure + flow panel
is shown so you can monitor gas conditions immediately.

---

### Step 3 — Logging

All data — spin frequency **and** both Alicats — is written to a **single unified
CSV file** with fixed columns:

```
timestamp, freq_hz,
pressure_bar_A, temperature_C_A, vol_flow_slm_A, mass_flow_slm_A, setpoint_A, gas_A,
pressure_bar_B, temperature_C_B, vol_flow_slm_B, mass_flow_slm_B, setpoint_B, gas_B
```

Columns for disconnected channels are written as empty strings, so every file has
the same schema and can be parsed identically.

**To start logging:**
1. Open the *Logging* tab and find the **Unified log** group
2. Click **Browse…** and choose a file path (e.g. `mas_log.csv`)
3. Click **▶ Start logging** — one row is written per second

The row-count updates live next to the button. Click **⏹ Stop logging** to close
the file safely. The same tab also has **Load historical log** for loading a
previous unified (or legacy Alicat) CSV back in for plotting.

> **Note:** the Alicat meters do not need to be connected to start the unified log.
> Disconnected channels are simply blank. Frequency is sampled from the most recently
> loaded PicoScope CSV row.

---

### Step 4 — Pressure control

The **Pressure control** panel (right of the Connection column, one per Alicat) shows
live readout tiles:

| Tile | Value |
|---|---|
| Pressure | barg (gauge, relative to the current display offset) |
| Temp | °C |
| Mass flow | slm |
| Setpoint | barg (gauge, exactly what you typed) |

**Setpoint convention** — you always enter setpoints as **gauge pressure (barg)**:

| You enter | Result |
|---|---|
| `1` | ~1 bar above atmosphere |
| `0.21` | 0.21 bar above atmosphere |
| `0` | valve fully closed (0 bar absolute sent to the device) |

Internally the device receives `gauge + LOCAL_ATMOS` (≈ 0.953 bar for Zürich), so the
applied pressure matches the number you type. This same convention is used by the
spin routines.

- **Set SP** — type a setpoint (barg) and press Enter or **Send**
- **Ramp** — set a ramp rate (bar/s); 0 = instant
- **Gas** — select gas type and the change is queued for the next poll cycle
- **Valve Off** — press and hold 2 seconds to send setpoint = 0 absolute (fully
  closes the valve); a progress arc appears while holding
- **Serial monitor…** — opens a live TX/RX log window for debugging serial
  communication

**Pressure display offset** (affects the *Pressure* tile only, not what is sent):

- **Set Zero** — capture the current live reading as the display zero, so the tile
  reads `0.000` at the pressure currently applied (label turns green)
- **Clear offset** — restore the default local atmospheric offset (~0.953 bar for
  Zürich at 408 m), the normal gauge reference
- **Abs zero** — set the offset to 0 so the tile shows raw absolute pressure
  (amber warning); useful for verifying the true absolute reading

---

### Step 5 — Display options

| Option | Effect |
|---|---|
| **Unit** | Hz / kHz / kRPM — applied to all axes |
| **Filter** | Smoothing applied to the frequency trace |
| **N** | Window size (samples) for the selected filter |
| **Despike** | Remove outliers > N×σ (MAD-based) and interpolate |
| **Thresh σ** | Outlier rejection threshold |
| **Mean line** | Toggle the mean dashed line on the frequency plot |
| **±1σ band** | Toggle the shaded standard-deviation band |
| **Show last** | Crop the time axis to the most recent window |
| **Auto-refresh** | Re-read the PicoScope CSV every N seconds |
| **Freq limits** | Hard upper/lower bounds — readings outside the range are dropped before any filtering or statistics |

#### Frequency limits

Enable the **Freq limits** group in the Frequency tab to discard readings outside
a chosen range. Set *Low* and *High* in any supported unit (Hz / kHz / kRPM). The
label below shows how many rows were kept and how many were dropped.

> **Tip:** set limits to exclude background noise or spurious PicoScope triggers before
> spin-up — e.g. Low = 5 kHz, High = 800 kHz when spinning at ~100–700 kHz.

#### Frequency filters

| Filter | Notes |
|---|---|
| None | Raw data — no modification |
| Mean | General smoothing, fast |
| Median | Spike-resistant, preserves step edges |
| Savitzky-Golay | Preserves peak shape; requires `scipy` |
| Gaussian | Maximum smoothness; requires `scipy` |

> **Tip for spin-up data**: Apply freq limits first, then Despike at 2.5 σ, then
> Median N=25 or Savitzky-Golay N=50.

---

### Step 6 — Routines

The **Routines** tab lets you run pre-programmed spin-up and spin-down sequences
on Alicat A. Each step is a *(setpoint, duration)* pair, where the setpoint is in
**gauge pressure (barg)** — the same convention as the manual Set SP field
(`0` closes the valve; `1` = ~1 bar above atmosphere). The routine runs in the
background and honours the current ramp rate.

- **Edit routines…** — opens a scrollable editor for spin-up and spin-down steps
  (Setpoint in barg, Duration in seconds)
- **▶ Spin UP / ▶ Spin DOWN** — start the selected sequence
- **⏸ Pause / ▶ Resume** — pause and resume mid-sequence
- **⏹ Stop** — abort immediately
- **Ramp** — bar/s ramp rate applied between steps (0 = instant jumps)

The status line shows the current step, the target setpoint, and whether the
routine is ramping, holding, paused, or complete.

---

### Step 7 — Export

Set a time range (or click **Use visible** to grab the current zoom window), then:

- **Save plot (PDF)** — vector figure, publication-ready, white background
- **Save plot (PNG)** — 300 dpi raster
- **Save data (CSV)** — spin frequency merged with Alicat A readings
  (nearest-timestamp match, ≤ 2 s tolerance)

---

## Unified log CSV format

Every row written during a logging session:

```
timestamp, freq_hz,
pressure_bar_A, temperature_C_A, vol_flow_slm_A, mass_flow_slm_A, setpoint_A, gas_A,
pressure_bar_B, temperature_C_B, vol_flow_slm_B, mass_flow_slm_B, setpoint_B, gas_B
```

- `timestamp` — `YYYY-MM-DD HH:MM:SS.mmm` (local time, ms precision)
- `freq_hz` — latest frequency reading from the loaded PicoScope CSV (Hz)
- Alicat columns — **raw values as reported by the device**: absolute pressure (bar),
  temperature (°C), volumetric and mass flow (slm), setpoint (absolute bar in pressure
  mode), gas name; empty if that unit is not connected
- The file is flushed after every row and is safe to inspect while recording
- Loading a unified file via **Load historical log** automatically extracts the
  Alicat A columns for plotting; legacy single-unit files are also supported

---

## Serial connection troubleshooting

If the Alicat is not responding, run the probe script (port must be free — disconnect
from the app first):

```bash
.venv/bin/python scripts/alicat_probe.py
```

This tests baud rates, RTS/DTR variants, Modbus RTU frames, loopback detection, and
raw byte timing — and prints a diagnosis at the end.

---

## Project layout

```
Tachometer/
├── scripts/
│   ├── mas_monitor.py    <- main application (run this)
│   └── alicat_probe.py   <- standalone serial diagnostics
├── examples/
│   └── MeasurementLog*.csv   <- example PicoScope frequency log
├── pyproject.toml
└── README.md
```
