# MAS Rotor Monitor

Live dashboard for optically-detected **Magic-Angle-Spinning (MAS) NMR rotors**.

It reads the frequency-log CSV produced by a PicoScope's built-in datalogging,
overlays live flow/pressure data from up to **two** Alicat mass-flow / pressure
controllers (A + B), lets you drive their setpoints (manually or via scripted
spin-up / spin-down routines), and logs everything to one tidy CSV — all in a
single dark-themed desktop GUI.

> **In one sentence:** point it at your PicoScope frequency log, optionally plug in
> your Alicat(s), and you get a live spin-rate dashboard with pressure control and
> publication-quality plot export.

The plot fills the top of the window; the controls below are organised into tabs:

| Tab | Purpose |
|---|---|
| **Frequency** | Load spin CSV, display options, smoothing filters, frequency limits |
| **Gas & Control** | Connect Alicats, pressure setpoint / gas / valve control |
| **Logging** | Unified CSV logging and historical-file loading |
| **Export** | Save plots (PDF/PNG) and merged data (CSV) |
| **Routines** | Pre-programmed spin-up / spin-down step sequences |

---

## Table of contents

1. [What you need](#1-what-you-need)
2. [How it all connects](#2-how-it-all-connects)
3. [Installing the software](#3-installing-the-software)
4. [Configuring your Alicat](#4-configuring-your-alicat)
5. [First run — a 5-minute walkthrough](#5-first-run--a-5-minute-walkthrough)
6. [Full workflow reference](#6-full-workflow-reference)
7. [The unified log CSV format](#7-the-unified-log-csv-format)
8. [Serial protocol reference](#8-serial-protocol-reference)
9. [Configuration constants](#9-configuration-constants)
10. [Troubleshooting](#10-troubleshooting)
11. [FAQ](#11-faq)
12. [Glossary](#12-glossary)
13. [Project layout](#13-project-layout)

---

## 1. What you need

### Hardware

| Item | Notes |
|---|---|
| **MAS probe with an optical tachometer output** | The standard fibre-optic spin-rate pickup on a MAS NMR probe. Produces a pulse train whose frequency = rotor spin rate. |
| **PicoScope oscilloscope** | Any PicoScope that PicoScope 6/7 software supports. It measures the optical signal's frequency and logs it to CSV. |
| **Alicat controller(s)** *(optional)* | One or two Alicat mass-flow or pressure controllers — typically a **drive** line and a **bearing** line. Without them you still get a live frequency dashboard. |
| **USB ↔ RS-485 adapter** *(per Alicat bus)* | Alicats speak serial. A half-duplex USB-RS485 adapter (FTDI-based recommended) connects them to the computer. Multiple Alicats can share one bus by giving each a unique address. |
| **Computer** | macOS, Linux, or Windows. Examples below use macOS/Homebrew paths. |

### Software

- **Python 3.13 or newer** (required for Tk 9, which the GUI uses).
- Python packages: `matplotlib`, `pandas`, `numpy`, `pyserial`, `pyftdi`, and
  optionally `scipy` (for the Savitzky-Golay and Gaussian smoothing filters).
- **PicoScope 6 or 7** desktop software (for capturing the frequency log).

---

## 2. How it all connects

```
   MAS probe optical pickup
            │  (pulse train, freq = spin rate)
            ▼
      ┌───────────┐      USB       ┌──────────────┐
      │ PicoScope │ ─────────────▶ │              │
      └───────────┘                │              │
                                   │  Your computer│
   Alicat A (drive)                │  (this app)   │
      │ RS-485                     │              │
      ├───────────┐   USB-RS485    │              │
   Alicat B (bearing) ───────────▶ │              │
      │ RS-485                     └──────────────┘
      └───────────┘
```

- **PicoScope → computer:** PicoScope software writes a CSV of the measured
  frequency. This app reads that CSV (live or after the fact). The app does **not**
  talk to the PicoScope hardware directly — PicoScope owns the scope; this app owns
  the CSV.
- **Alicat(s) → computer:** over RS-485 via a USB adapter. Two Alicats can share one
  adapter/bus as long as they have different addresses (e.g. `A` and `B`).

### RS-485 wiring note

Most USB-RS485 adapters are **half-duplex** and use the **RTS** line to switch the
transceiver between transmit (DE, driver enable) and receive (RE, receiver enable).
This app handles that automatically: it raises RTS, sends the command, then drops RTS
to listen for the reply. If your adapter does this in hardware (auto-direction), it
still works — the RTS toggling is harmless.

Wire the bus as: adapter **A/D+ ↔ Alicat D+**, **B/D− ↔ Alicat D−**, and common
**GND ↔ GND**. Termination resistors are usually unnecessary at these short cable
lengths and baud rates.

---

## 3. Installing the software

**Step 1 — create the virtual environment (once).**

Requires Python 3.13+ for Tk 9. On macOS, install it with Homebrew first
(`brew install python@3.13`), then:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` includes `scipy` (optional — it enables the Savitzky-Golay and
Gaussian filters). If you'd rather skip it, install the core packages directly:

```bash
.venv/bin/pip install matplotlib pandas numpy pyserial pyftdi
```

On Linux/Windows, substitute your Python 3.13 interpreter path. The rest is identical.

**Step 2 — run it.**

```bash
.venv/bin/python scripts/mas_monitor.py
```

You can also install it as a console script (`mas-monitor`) via `pip install -e .`
using the included `pyproject.toml`.

> **No Alicat? No problem.** The app launches and runs fine without `pyserial` or any
> hardware — you just won't see the serial-connection controls. It's still a fully
> working frequency viewer.

---

## 4. Configuring your Alicat

Before the app can talk to an Alicat, set the device up from its front panel (or
Alicat's own software):

1. **Communication mode:** ASCII / serial (not Modbus). This app uses Alicat's
   plain-text ASCII protocol.
2. **Baud rate:** match what you'll select in the app. **19200** is the default here.
3. **Unit ID (address):** give each Alicat a unique single-letter address. Use `A`
   for the first (drive) and `B` for the second (bearing). These are the app's defaults.
4. **Control mode:** put the controller in **pressure control** mode (closed-loop on
   its pressure sensor) if you're controlling pressure.
5. **Device ramp rate:** set the Alicat's *internal* ramp to its **maximum** (or off).
   This app does its own software-side ramping between setpoints, so you want the
   device itself to respond as fast as possible. (If the device also ramps slowly,
   the two ramps fight each other.)

To check the wiring and that the device answers before launching the GUI, use the
bundled probe script (see [Troubleshooting](#10-troubleshooting)).

---

## 5. First run — a 5-minute walkthrough

This gets you from nothing to a live, logging dashboard.

1. **Capture frequency in PicoScope.** In PicoScope 6/7: add a *Frequency*
   measurement on the channel your optical pickup is wired to, then *File → Save As →
   Data Logging* and start logging. PicoScope appends rows to a CSV as it runs.

2. **Load that CSV.** Launch the app, go to the **Frequency** tab, click **Open…**,
   and pick the PicoScope CSV. The spin-rate trace appears immediately.

3. **Go live.** Still in the Frequency tab, tick **Auto** and set the refresh interval
   (e.g. 2 s). The app now re-reads the file on that interval, so the plot grows as
   PicoScope keeps logging.

4. **Connect an Alicat (optional).** Open the **Gas & Control** tab. In the
   *Connection* column pick the serial **Port**, set **Baud** (19200) and **Addr**
   (`A`), then click **Connect**. Live pressure/flow tiles light up and a pressure +
   flow panel joins the plot. Connect Alicat B the same way — its control panel
   appears automatically.

5. **Apply a pressure (optional).** In the **Alicat A pressure control** panel, type a
   setpoint in **barg** into *Set SP* and press Enter. `0.5` means 0.5 bar above
   atmosphere. (See the setpoint convention in step 4 of the reference below.)

6. **Log everything.** Open the **Logging** tab, click **Browse…** to choose a file
   (e.g. `mas_log.csv`), then **▶ Start logging**. One row per second is written,
   combining frequency and both Alicats into a single CSV.

7. **Export a figure.** When you have a window worth keeping, go to **Export**, click
   **Use visible** to grab the current zoom, then **Save plot (PDF)**.

That's the whole loop. Everything below is reference detail.

---

## 6. Full workflow reference

### Step 1 — Load spin frequency data

#### Option A — PicoScope live logging

In PicoScope 6 or 7:
- Add a *Frequency* measurement on the optical-pickup channel
- *File → Save As → Data Logging* and start logging

PicoScope writes a semicolon-delimited CSV (note the comma decimal separator):

```
Time (UTC +02:00 yyyy-MM-dd HH:mm:ss);Frequency (A) (Hz)
2024-01-15 13:11:21;63493,363
```

Click **Open…** in the Frequency tab to load it. Tick **Auto** + set a refresh
interval to keep the view live as PicoScope appends new rows.

> The loader is tolerant: blank cells, `NaN` entries, and malformed rows are skipped,
> and the comma decimal separator is handled automatically.

#### Option B — Load old files

- **Spin data:** click **Open…** in the *Frequency* tab to load any historical PicoScope CSV
- **Unified log:** click **Load file** in the *Logging* tab to load a previously-saved
  unified log CSV (see [§7](#7-the-unified-log-csv-format))

---

### Step 2 — Connect Alicats (optional)

Two Alicat meters can be connected independently (Alicat A and Alicat B).

1. Select the serial **Port**, **Baud** (usually 19200), and device **Addr** (default `A` / `B`)
2. Click **Connect** (use **⟳** to rescan ports if your adapter isn't listed)

The Alicat starts streaming immediately once connected. Alicat B's control panel
appears automatically when it is connected, and disappears when disconnected.

The 4-panel view activates automatically when both spin and Alicat data are available:

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

When **both** Alicats are connected, the pressure + flow panel shows both sensors
together — Alicat A as **solid** lines and Alicat B as **dashed** lines, with a
combined legend (Pressure A/B, Mass flow A/B). This applies to both the live view
and exported plots. The scatter panels (Freq vs pressure / flow) use Alicat A.

---

### Step 3 — Logging

All data — spin frequency **and** both Alicats — is written to a **single unified
CSV file** with fixed columns:

```
timestamp, freq_hz,
pressure_bar_A, temperature_C_A, vol_flow_slm_A, mass_flow_slm_A, setpoint_A, gas_A,
pressure_bar_B, temperature_C_B, vol_flow_slm_B, mass_flow_slm_B, setpoint_B, gas_B
```

Columns for disconnected channels are written as empty strings, so **every file has
the same schema and can be parsed identically** — no matter what was connected.

**To start logging:**
1. Open the *Logging* tab and find the **Unified log** group
2. Click **Browse…** and choose a file path (e.g. `mas_log.csv`)
3. Click **▶ Start logging** — one row is written per second

The row-count updates live next to the button. Click **⏹ Stop logging** to close
the file safely. The same tab also has **Load historical log** for loading a previous
unified (or legacy Alicat) CSV back in for plotting.

> **Note:** the Alicat meters do not need to be connected to start the unified log.
> Disconnected channels are simply blank. Frequency is sampled from the most recently
> loaded PicoScope CSV row, so keep **Auto-refresh** on for live frequency logging.

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
applied pressure matches the number you type. This same convention is used by the spin
routines.

- **Set SP** — type a setpoint (barg) and press Enter or **Send**
- **Ramp** — set a ramp rate (bar/s); 0 = instant. The app ramps the setpoint in
  small increments at this rate, so the device's own ramp should be set to maximum.
- **Gas** — select gas type; the change is queued for the next poll cycle
- **Valve Off** — **press and hold for 2 seconds** to send setpoint = 0 absolute
  (fully closes the valve); a progress arc fills while holding. This is the emergency
  stop.
- **Serial monitor…** — opens a live TX/RX log window (with a manual send field) for
  debugging serial communication

**Pressure display offset** (affects the *Pressure* tile only, not what is sent):

- **Set Zero** — capture the current live reading as the display zero, so the tile
  reads `0.000` at the pressure currently applied (label turns green)
- **Clear offset** — restore the default local atmospheric offset (~0.953 bar for
  Zürich at 408 m), the normal gauge reference
- **Abs zero** — set the offset to 0 so the tile shows raw absolute pressure (amber
  warning); useful for verifying the true absolute reading

---

### Step 5 — Display options (Frequency tab)

| Option | Effect |
|---|---|
| **Unit** | Hz / kHz / kRPM — applied to all frequency axes |
| **Filter** | Smoothing applied to the frequency trace |
| **N** | Window size (samples) for the selected filter |
| **Despike** | Remove outliers > N×σ (MAD-based) and interpolate over the gaps |
| **Thresh σ** | Outlier rejection threshold for despiking |
| **Mean line** | Toggle the mean dashed line on the frequency plot |
| **±1σ band** | Toggle the shaded standard-deviation band |
| **Show last** | Crop the time axis to the most recent window (30 s … 30 min) |
| **Auto-refresh** | Re-read the PicoScope CSV every N seconds |
| **Freq limits** | Hard upper/lower bounds — readings outside the range are dropped before any filtering or statistics |

> **Unit note:** `kRPM = Hz × 0.06` (1 Hz = 60 RPM). So 100 kHz ≈ 6000 kRPM.

#### Frequency limits

Enable the **Freq limits** group to discard readings outside a chosen range. Set
*Low* and *High* in any supported unit (Hz / kHz / kRPM). The label below shows how
many rows were kept and how many were dropped. Limits are applied **first**, before
despiking, smoothing, and statistics — so excluded points never skew the mean/σ.

> **Tip:** set limits to exclude the noise floor or spurious PicoScope triggers — e.g.
> Low = 5 kHz, High = 800 kHz when spinning at ~100–700 kHz.

#### Frequency filters

| Filter | Notes |
|---|---|
| None | Raw data — no modification |
| Mean | General smoothing, fast |
| Median | Spike-resistant, preserves step edges |
| Savitzky-Golay | Preserves peak shape; requires `scipy` |
| Gaussian | Maximum smoothness; requires `scipy` |

All filters and the despiker are **NaN-aware**: real PicoScope logs contain `NaN`
gaps (dropped measurements), and these are interpolated internally so the smoothing
filters work correctly instead of blanking the trace.

> **Recommended for spin-up data:** set freq limits first → Despike at 2.5 σ → then
> Median N=25 or Savitzky-Golay N=50.

---

### Step 6 — Routines

The **Routines** tab runs pre-programmed spin-up and spin-down sequences on Alicat A.
Each step is a *(setpoint, duration)* pair, where the setpoint is in **gauge pressure
(barg)** — the same convention as the manual Set SP field (`0` closes the valve;
`1` ≈ 1 bar above atmosphere). The routine runs in a background thread and honours the
current ramp rate.

- **Edit routines…** — opens a scrollable editor for spin-up and spin-down steps
  (Setpoint in barg, Duration in seconds; add/remove rows, then **✓ Save** each panel)
- **▶ Spin UP / ▶ Spin DOWN** — start the selected sequence
- **⏸ Pause / ▶ Resume** — pause and resume mid-sequence
- **⏹ Stop** — abort immediately
- **Ramp** — bar/s ramp rate applied between steps (0 = instant jumps)

The status line shows the current step, the target setpoint, and whether the routine
is ramping, holding, paused, or complete.

---

### Step 7 — Export

Set a time range (or click **Use visible** to grab the current zoom window), then:

- **Save plot (PDF)** — vector figure, publication-ready, white background
- **Save plot (PNG)** — 300 dpi raster
- **Save data (CSV)** — spin frequency merged with Alicat A readings
  (nearest-timestamp match, ≤ 2 s tolerance)

---

## 7. The unified log CSV format

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
  Alicat A columns for plotting; legacy single-unit logs are also supported

Because the schema is fixed, you can load any session's CSV — one Alicat, two, or none
— with the same parser (e.g. `pandas.read_csv`). Empty channels are simply `NaN`.

---

## 8. Serial protocol reference

This app speaks Alicat's plain-text **ASCII** protocol. Useful if you're debugging
with the Serial monitor or an external terminal. Commands are terminated with a
carriage return (`\r`, **not** `\r\n`).

| Action | Command sent | Example |
|---|---|---|
| Poll a reading | `<addr>\r` | `A\r` |
| Set setpoint | `<addr>S<value>\r` | `AS1.9530\r` |
| Set device ramp rate | `<addr>SR<value>\r` | `ASR9999.0\r` |
| Set gas by ID | `<addr>$$G<id>\r` | `A$$G8\r` (N₂) |

**Serial parameters:** 8 data bits, no parity, 1 stop bit (8N1); baud as selected
(default 19200); 1 s read/write timeout. The bus is polled at **2 Hz** per device.

**Gas IDs:**

| ID | Gas | ID | Gas | ID | Gas |
|---|---|---|---|---|---|
| 0 | Air | 3 | CO | 6 | H₂ |
| 1 | Ar | 4 | CO₂ | 7 | He |
| 2 | CH₄ | 5 | C₂H₆ | 8 | N₂ |

A typical Alicat streaming reply has the form
`<addr> <pressure> <temp> <vol_flow> <mass_flow> <setpoint> <gas>` — the app maps
whatever fields are present and ignores extras.

---

## 9. Configuration constants

A few constants near the top of `scripts/mas_monitor.py` control defaults you may want
to change for your site:

| Constant | Default | What it is |
|---|---|---|
| `LOCAL_ATMOS` | `0.953` | **Local atmospheric pressure in bar absolute.** Used to convert your barg setpoints to the absolute values the device expects. **Set this to your site's atmospheric pressure** (sea level ≈ `1.01325`; higher altitude = lower value). Zürich (~408 m) ≈ `0.953`. |
| `ALICAT_BAUD` | `19200` | Default baud rate selected in the UI |
| `UNIT_MULTS` | — | Frequency unit conversions (`Hz`, `kHz`, `kRPM = Hz × 0.06`) |
| `poll_hz` | `2.0` | Per-device serial polling rate (in `AlicatLogger.__init__`) |

> **Why `LOCAL_ATMOS` matters:** if it's wrong, a gauge setpoint of `1` won't produce
> exactly 1 bar above your actual atmosphere. Measure your local barometric pressure
> (in bar) and set the constant to match for accurate gauge readings.

---

## 10. Troubleshooting

### The Alicat won't connect / no readings appear

Run the bundled probe script first. **The port must be free**, so disconnect the
Alicat in the app (or close the app) before running it:

```bash
.venv/bin/python scripts/alicat_probe.py
```

It tries multiple baud rates, RTS/DTR variants, Modbus RTU frames, loopback detection,
and raw byte timing, then prints a diagnosis. To scan addresses or target a port:

```bash
.venv/bin/python scripts/alicat_probe.py --port /dev/cu.usbserial-XXXX --scan-units
```

Common causes:

| Symptom | Likely cause / fix |
|---|---|
| Port not in the dropdown | Adapter not plugged in, or driver missing. Click **⟳** to rescan. FTDI adapters need the FTDI VCP driver on some systems. |
| Connects but "waiting for response…" | Wrong **baud** or wrong **address**. Match them to the Alicat's front-panel settings. |
| Garbage / null bytes in Serial monitor | D+/D− swapped, or wrong baud. Try swapping the two RS-485 data lines. |
| Two Alicats, only one answers | Both share the same address. Give them distinct unit IDs (`A`, `B`). |
| Setpoint doesn't take effect | Alicat not in the right control mode, or its internal ramp is slow — set the device ramp to max. |

### The plot is blank or `tight_layout` warnings

Make sure a CSV is actually loaded (Frequency tab → **Open…**). The figure manages its
own layout, so any leftover `tight_layout` warning from older versions is resolved.

### Smoothing filters do nothing

Savitzky-Golay and Gaussian require **scipy**: `.venv/bin/pip install scipy`. Without
it, those two fall back to a rolling mean.

### Gauge pressures look off by ~1 bar

Set [`LOCAL_ATMOS`](#9-configuration-constants) to your true local atmospheric pressure.

---

## 11. FAQ

**Do I need the Alicats to use this?**
No. Without `pyserial` or any hardware it's a full-featured frequency viewer and plot
exporter.

**Does the app control the PicoScope?**
No. PicoScope captures and logs the frequency to CSV; this app reads that CSV. Keep
PicoScope's data logging running for live updates.

**Can I log without anything connected?**
Yes — the unified log writes a row per second regardless; disconnected channels are
blank, and frequency comes from the loaded CSV.

**What does "Valve Off" do, and why hold it?**
It sends a true 0-bar-absolute setpoint to fully close the valve. The 2-second hold
prevents accidental triggering — it's the emergency stop.

**Why enter setpoints in barg instead of absolute?**
So the number you type equals the pressure above atmosphere you actually apply. `0`
always means "closed". The app adds `LOCAL_ATMOS` before talking to the device.

**Can the second Alicat (B) drive the plots?**
The time-series and scatter plots use Alicat A. Alicat B is still controlled, read,
and logged — it's just not the plotting source.

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **MAS** | Magic-Angle Spinning — the rotor technique this monitors |
| **barg** | Bar **gauge** — pressure relative to local atmosphere (what you type) |
| **bar absolute** | Pressure relative to vacuum (what the device works in) |
| **Drive / Bearing** | The two gas lines of a MAS probe: drive spins the rotor, bearing floats it |
| **slm** | Standard litres per minute (mass-flow unit) |
| **MAD** | Median Absolute Deviation — robust spread estimate used by the despiker |
| **RS-485** | The differential serial bus the Alicats use |
| **DE/RE** | Driver-Enable / Receiver-Enable on a half-duplex RS-485 transceiver, toggled via RTS |

---

## 13. Project layout

```
Tachometer/
├── scripts/
│   ├── mas_monitor.py        <- main application (run this)
│   └── alicat_probe.py       <- standalone serial diagnostics
├── examples/
│   └── MeasurementLog*.csv   <- example PicoScope frequency log
├── requirements.txt          <- pip dependencies
├── pyproject.toml
└── README.md
```

---

## License

Released under the MIT License — see [`LICENSE`](LICENSE) for the full text.

---

*Built for optically-detected MAS NMR rotor monitoring. Runs fully offline; no data
leaves your machine.*
