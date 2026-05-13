# MAS Rotor Monitor

Live dashboard for optically-detected MAS NMR rotors.

Reads the frequency-log CSV from PicoScope's built-in datalogging, overlays live
Alicat flow/pressure data, and lets you control the Alicat setpoint — all in one
dark-themed GUI.

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
Time (UTC +02:00 yyyy-MM-dd HH:mm:ss);Frequency (A) (Hz);Frequency (A) (Hz)
2024-01-15 13:11:21;63493,363;63493,363
```

Click **Open…** in the top bar to load it. Tick **Auto** + set a refresh interval
(e.g. 2 s) to keep the view live as PicoScope appends new rows.

#### Option B — Load old files

- **Spin data**: click **Open…** in the top bar to load any historical PicoScope CSV
- **Alicat data**: click **Open…** in the *Alicat → Load file* row to load a previously
  saved Alicat log CSV

Both sources are merged by timestamp and shown together automatically.

---

### Step 2 — Connect Alicat (optional)

1. Select the serial port, baud rate (usually 19200), and device address (default `A`)
2. Click **Connect**

The Alicat starts streaming immediately once connected — no need to press
*Start logging* to see live plots. Press **▶ Start logging** when you also want
readings written to a CSV file (set the path with **Browse…** first).

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

If only Alicat is connected (no spin file loaded), a single pressure + flow panel
is shown so you can monitor gas conditions immediately.

---

### Step 3 — Pressure control

The **Pressure control** panel (right of the Alicat group) shows live readout tiles:

| Tile | Value |
|---|---|
| Pressure | bar |
| Temp | °C |
| Mass flow | slm |
| Setpoint | slm |

- **Set SP** — type a new setpoint and press Enter or **Send**
- **Gas** — select gas type (Air / Ar / CO₂ / N₂ / O₂ / He / H₂ / N₂O) and the
  change is queued for the next poll cycle

The **Serial monitor…** button opens a live TX/RX log window for debugging serial
communication, including a manual send field.

---

### Step 4 — Display options

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

#### Frequency filters

| Filter | Notes |
|---|---|
| None | Raw data — no modification |
| Mean | General smoothing, fast |
| Median | Spike-resistant, preserves step edges |
| Savitzky-Golay | Preserves peak shape; requires `scipy` |
| Gaussian | Maximum smoothness; requires `scipy` |

> **Tip for spin-up data**: Despike at 2.5 σ first, then Median N=25 or
> Savitzky-Golay N=50.

---

### Step 5 — Export

Set a time range (or click **Use visible** to grab the current zoom window), then:

- **Save plot (PDF)** — vector figure, publication-ready, white background
- **Save plot (PNG)** — 300 dpi raster
- **Save data (CSV)** — spin frequency merged with Alicat readings
  (nearest-timestamp match, ≤ 2 s tolerance)

---

## Alicat CSV format

When logging is active, every serial poll is appended in real time:

```
timestamp, pressure_bar, temperature_C, vol_flow_slm, mass_flow_slm, setpoint, gas
```

The file is flushed after every row and is safe to inspect while recording.

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
├── pyproject.toml
└── README.md
```
