# MAS Rotor Monitor

Live dashboard for optically-detected MAS NMR rotors.

Reads the frequency-log CSV from PicoScope's built-in datalogging, overlays live
Alicat flow/pressure data from up to **two** Alicat meters (A + B), and lets you
control their setpoints — all in one dark-themed GUI.

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
- **Unified log**: click **Load file** in the *Gas & Control → Connection* column to
  load a previously-saved unified log CSV (see §Logging below)

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
1. In the *Gas & Control* tab, find **Unified log** at the bottom of the Connection column
2. Click **Browse…** and choose a file path (e.g. `mas_log.csv`)
3. Click **▶ Start logging** — one row is written per second

The row-count updates live next to the button. Click **⏹ Stop logging** to close
the file safely.

> **Note:** the Alicat meters do not need to be connected to start the unified log.
> Disconnected channels are simply blank. Frequency is sampled from the most recently
> loaded PicoScope CSV row.

---

### Step 4 — Pressure control

The **Pressure control** panel (right of the Connection column, one per Alicat) shows
live readout tiles:

| Tile | Value |
|---|---|
| Pressure | bar (gauge) |
| Temp | °C |
| Mass flow | slm |
| Setpoint | slm |

- **Set SP** — type a new setpoint (gauge pressure) and press Enter or **Send**
- **Ramp** — set a ramp rate (bar/s); 0 = instant
- **Gas** — select gas type and the change is queued for the next poll cycle
- **Zero P** — set pressure offset to 0 (absolute mode; amber warning; use to
  fully close the valve in an emergency)
- **Clear offset** — restore the default local atmospheric offset (~0.953 bar for
  Zürich at 408 m)
- **Valve Off** — press and hold 2 seconds to send setpoint = 0 absolute; a progress
  arc appears while holding
- **Serial monitor…** — opens a live TX/RX log window for debugging serial
  communication

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

The **Routines** tab has two sections:

#### Spin routine (open-loop step sequences)

Define pre-programmed spin-up and spin-down sequences. Each step is a
*(setpoint, duration)* pair. The routine runs in the background and honours
the current ramp rate.

- **Edit routines…** — opens a scrollable editor for spin-up and spin-down steps
- **▶ Spin UP / ▶ Spin DOWN** — start the selected sequence
- **⏸ Pause / ▶ Resume** — pause and resume mid-sequence
- **⏹ Stop** — abort immediately

#### Frequency Control (closed-loop feedback)

Automatically adjusts Alicat setpoints to reach and hold a target spin frequency.
**Requires a live PicoScope frequency reading with Auto-refresh enabled.**

**Mode — Drive only (1 Alicat)**

A proportional controller adjusts Alicat A (Drive) to minimise the error between
the current frequency and the target. Safe upper/lower pressure limits are enforced
at all times.

**Mode — Drive + Bearing (2 Alicats) — Bruker sequence**

Follows the standard Bruker MAS spin-up protocol:

| Phase | Action |
|---|---|
| 1 — Bearing | Apply bearing start pressure; wait for rotor to float |
| 2 — Drive | Apply drive start pressure; wait until rotor begins spinning (> 5 % of target) |
| 3 — Frequency control | Proportional drive correction + periodic bearing wobble |

The **bearing wobble** is a hill-climbing optimisation: every N seconds the bearing
pressure is stepped ±Δ bar and the frequency stability (σ over the last 10 readings)
is measured. The step that improves stability is accepted; the other direction is tried
next cycle.

**Parameters**

| Field | Description |
|---|---|
| Target | Desired spin frequency (Hz / kHz / kRPM) |
| Stability n / σ | Require σ < threshold over last n readings before engaging |
| Min / Max SP | Hard pressure limits for each Alicat during closed-loop operation |
| Start SP | Initial setpoint applied at the start of each phase |
| Step | Minimum setpoint increment (used internally) |
| Gain | Proportional gain: ΔSP = gain × error (bar / kHz) |
| Wobble Δ | Bearing search step size (bar) |
| Wobble every | Bearing optimisation interval (s) |

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
- Alicat columns — raw absolute pressure (bar), temperature (°C), volumetric and
  mass flow (slm), setpoint (slm), gas name; empty if that unit is not connected
- The file is flushed after every row and is safe to inspect while recording
- Loading a unified file via **Load file** automatically extracts the Alicat A columns
  for plotting; legacy single-unit files are also supported

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
