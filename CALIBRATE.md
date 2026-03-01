# Calibration

The scale must be calibrated once after assembly to convert raw HX711 ADC counts into pounds. The result is saved to `calibration.cfg` and loaded automatically by `celestial_scale.py` on startup — no code editing required.

## What you need

- A known reference weight (a dumbbell, a bag of dog food with a printed weight, etc.)
- SSH access to the Pi

## Procedure

### 1. Stop the kiosk service

The kiosk and the calibration tool both use the HX711; only one can hold the GPIO pins at a time.

```bash
sudo systemctl stop celestial_scale
```

### 2. Run the calibration tool

```bash
cd /home/oas/celestial_scale
python3 calibrate.py
```

### 3. Follow the prompts

**Step 1 — Tare:** Remove everything from the scale platform and press Enter. The tool captures 20 baseline readings.

**Step 2 — Known weight:** Place your reference weight on the platform, enter its weight in pounds when prompted, and wait. The tool captures 20 loaded readings.

The factor is computed and written to `calibration.cfg` automatically:

```
========================================
CALIBRATION SUCCESSFUL
Known Weight:     50.0 lbs
Net Raw Value:    21000.0
Calibration Factor: 420.0000
Saved to:         /home/oas/celestial_scale/calibration.cfg
========================================
```

### 4. Restart the service

```bash
sudo systemctl start celestial_scale
```

The new factor takes effect immediately on restart. No code changes needed.

---

## How it works

`calibration.cfg` is a plain INI file written by `calibrate.py`:

```ini
[scale]
calibration_factor = 420.0000
```

`celestial_scale.py` reads this file at startup. If the file is missing, it falls back to a built-in default and prints a warning — check the journal if readings seem off:

```bash
sudo journalctl -u celestial_scale -n 20
```

## Re-calibrating

Run `calibrate.py` again at any time — it overwrites `calibration.cfg` with the new value. Useful after replacing load cells or the HX711 board.

## Troubleshooting

- **Inconsistent readings:** Ensure the scale is on a flat, hard surface and the platform isn't touching anything on the sides.
- **Negative weights:** If the reading decreases when load is applied, the A+ and A- wires on the HX711 are swapped.
- **Sensor disconnected:** Ensure pigpiod is running — `sudo systemctl start pigpiod`.
