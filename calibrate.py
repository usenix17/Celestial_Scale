#!/usr/bin/env python3
"""Celestial Scale Calibration Tool.

Interactive command-line utility that determines the HX711 calibration
factor (SCALE_RATIO) by comparing raw ADC counts against a known
reference weight. The resulting value is used by the main scale
application to convert raw readings into pounds.

Procedure:
    1. Clear the scale and capture the zero offset (tare).
    2. Place a known weight and capture the loaded reading.
    3. Compute SCALE_RATIO = (loaded_raw - zero_raw) / known_weight_lbs.

Requires:
    - pigpiod running (sudo systemctl start pigpiod)
    - HX711 wired to GPIO 5 (DOUT) and GPIO 6 (SCK)
"""

import configparser
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

import pigpio

CALIBRATION_CONFIG = Path(__file__).resolve().parent / "calibration.cfg"
"""Path: Config file written by this tool and read by celestial_scale.py."""

# ----------------------------
# Hardware Pins (must match celestial_scale.py)
# ----------------------------
DOUT: int = 5
"""int: BCM GPIO pin for HX711 data output (Physical Pin 29)."""

SCK: int = 6
"""int: BCM GPIO pin for HX711 serial clock (Physical Pin 31)."""

# ----------------------------
# Sampling Config
# ----------------------------
SAMPLE_COUNT: int = 20
"""int: Number of readings to capture per calibration step."""

SAMPLE_DELAY: float = 0.1
"""float: Delay in seconds between successive readings."""

READ_TIMEOUT: float = 2.0
"""float: Maximum time in seconds to wait for the HX711 to be ready."""


def get_raw_reading(pi: pigpio.pi) -> Optional[int]:
    """Reads a single raw 24-bit signed value from the HX711.

    Bit-bangs the HX711 serial protocol using pigpio. Waits for DOUT
    to go low (data ready), clocks out 24 bits, then sends a 25th
    pulse to set the gain to 128 for the next conversion.

    Args:
        pi: A connected pigpio.pi() instance with DOUT and SCK pins
            already configured.

    Returns:
        A signed integer representing the raw ADC value, or None if
        the HX711 did not become ready within READ_TIMEOUT seconds.
    """
    deadline = time.time() + READ_TIMEOUT
    while pi.read(DOUT):
        if time.time() > deadline:
            return None

    raw = 0
    for _ in range(24):
        pi.write(SCK, 1)
        raw = (raw << 1) | pi.read(DOUT)
        pi.write(SCK, 0)

    # 25th pulse sets gain to 128 for next reading
    pi.write(SCK, 1)
    pi.write(SCK, 0)

    # Convert from 24-bit two's complement
    if raw & 0x800000:
        raw -= 0x1000000

    return raw


def capture_readings(pi: pigpio.pi, count: int = SAMPLE_COUNT,
                     delay: float = SAMPLE_DELAY) -> list[int]:
    """Captures multiple raw HX711 readings with progress feedback.

    Collects up to ``count`` valid readings, printing a dot for each
    successful sample. Readings that return None (timeouts) are silently
    skipped.

    Args:
        pi: A connected pigpio.pi() instance with DOUT and SCK pins
            already configured.
        count: Number of readings to attempt.
        delay: Delay in seconds between successive readings.

    Returns:
        A list of raw signed integer ADC values. May be shorter than
        ``count`` if some readings timed out.
    """
    readings: list[int] = []
    for _ in range(count):
        val = get_raw_reading(pi)
        if val is not None:
            readings.append(val)
            print(".", end="", flush=True)
        time.sleep(delay)
    return readings


def main() -> None:
    """Runs the interactive calibration procedure.

    Connects to pigpiod, captures zero-offset and loaded readings,
    computes the SCALE_RATIO, and prints the result for use in the
    main scale application.

    Raises:
        SystemExit: If pigpiod is not running.
    """
    pi = pigpio.pi()
    if not pi.connected:
        print("Error: pigpiod not running. "
              "Run 'sudo systemctl start pigpiod'")
        sys.exit(1)

    pi.set_mode(DOUT, pigpio.INPUT)
    pi.set_mode(SCK, pigpio.OUTPUT)

    print("\n--- Celestial Scale Calibration Tool ---")

    # Step 1: Capture zero offset
    print("1. Clear the scale (remove all weight).")
    input("Press Enter when scale is empty...")

    print("Capturing zero offset...", end="", flush=True)
    zero_readings = capture_readings(pi)

    if not zero_readings:
        print("\nError: No readings captured. Check wiring!")
        pi.stop()
        sys.exit(1)

    offset = statistics.median(zero_readings)
    print(f"\nZero Offset (Tare): {offset}")

    # Step 2: Capture loaded reading
    print("\n2. Place a KNOWN weight on the scale (e.g., 50 lbs).")
    known_weight = float(input("Enter the weight in lbs: "))

    print(f"Reading {known_weight} lbs...", end="", flush=True)
    weight_readings = capture_readings(pi)

    pi.stop()

    if not weight_readings:
        print("\nError: No readings captured. Check wiring!")
        sys.exit(1)

    raw_val = statistics.median(weight_readings)
    net_raw = raw_val - offset

    if net_raw == 0:
        print("\nError: No weight detected. Check your wiring!")
        sys.exit(1)

    # Step 3: Compute and display result
    scale_factor = net_raw / known_weight

    # Step 4: Write result to calibration.cfg
    cfg = configparser.ConfigParser()
    cfg["scale"] = {"calibration_factor": f"{scale_factor:.4f}"}
    with open(CALIBRATION_CONFIG, "w") as f:
        cfg.write(f)

    print("\n" + "=" * 40)
    print("CALIBRATION SUCCESSFUL")
    print(f"Known Weight:     {known_weight} lbs")
    print(f"Net Raw Value:    {net_raw}")
    print(f"Calibration Factor: {scale_factor:.4f}")
    print(f"Saved to:         {CALIBRATION_CONFIG}")
    print("=" * 40)
    print("\nRestart the celestial_scale service to apply:")
    print("  sudo systemctl restart celestial_scale")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
