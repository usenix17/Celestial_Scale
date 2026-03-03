#!/usr/bin/env python3
# pylint: disable=too-many-lines
"""Celestial Scale - Planetary Weight Display Kiosk.

Reads weight from a load cell ADC and displays the user's weight on
various celestial bodies through a pygame UI on an HDMI-connected screen.

Hardware:
    - HX711 (pigpio bit-bang) or NAU7802 (I2C) load cell ADC
    - Maintenance button on GPIO 18 (Physical Pin 12)
    - HDMI display via Raspberry Pi Zero

Features:
    - Instant software tare with continuous zero tracking (CZT)
    - Median-filtered spike rejection for noisy load cell environments
    - Motion-aware CZT that won't tare out slow-stepping users
    - Variance-based weight lock-in during calculation
    - Dirty-rect rendering to minimize CPU load on single-core Pi Zero
    - Pre-rendered static text surfaces for efficient blitting
    - Systemd watchdog integration for automatic crash recovery
    - Button: <5s press = Tare, 5× rapid = Calibration, 10s+ hold = Shutdown

Requires:
    - HX711: pigpiod running (systemctl enable pigpiod)
    - NAU7802: smbus2 + i2c-dev kernel module
    - python3-pygame
"""

import argparse
import json
import logging
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pygame

from adc import HX711WeightReader, NAU7802WeightReader

_log = logging.getLogger(__name__)

try:
    from gpiozero import Button
except ImportError:
    Button = None  # type: ignore[assignment,misc]

# ----------------------------
# Config & Tuning
# ----------------------------
WINDOWED_DEV = False
"""bool: Run in windowed mode for development. False = fullscreen."""

TRIGGER_THRESHOLD_LB = 20.0
"""float: Minimum weight in pounds to trigger the calculation screen."""

CALC_SECONDS = 3
"""int: Maximum duration of calculation phase in seconds."""

CALC_VARIANCE_THRESHOLD = 1.0
"""float: Weight variance (lbs) below which lock-in occurs during calc."""

RESULTS_TIMEOUT = 30.0
"""float: Maximum time in seconds to display results before returning to idle."""

STEP_OFF_DELAY = 5.0
"""float: Grace period in seconds after stepping off before returning to idle."""

BUTTON_PIN_BCM = 18
"""int: BCM GPIO pin number for the tare/shutdown button (Physical Pin 12)."""

SHUTDOWN_HOLD_SEC = 10.0
"""float: Button hold duration in seconds to trigger safe shutdown."""

HX711_DOUT_PIN = 5
"""int: BCM GPIO pin for HX711 data output (Physical Pin 29)."""

HX711_SCK_PIN = 6
"""int: BCM GPIO pin for HX711 serial clock (Physical Pin 31)."""

_CALIBRATION_DEFAULT_FACTOR = 420.0
_CALIBRATION_CONFIG = Path(__file__).resolve().parent / "calibration.json"


def _load_calibration():
    """Loads (zero_offset_raw, calibration_factor) from calibration.json.

    calibration.json is written by calibrate.py after running the on-screen
    calibration procedure and lives in the same directory as this script.
    If the file is missing or unreadable, defaults are returned and a
    warning is logged.

    Returns:
        tuple[int, float]: ``(zero_offset_raw, calibration_factor)`` where
        ``zero_offset_raw`` is the raw ADC count at zero load and
        ``calibration_factor`` converts net raw counts to pounds.
    """
    if _CALIBRATION_CONFIG.exists():
        try:
            data = json.loads(_CALIBRATION_CONFIG.read_text(encoding="utf-8"))
            return int(data["zero_offset"]), float(data["calibration_factor"])
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            _log.warning("Could not read calibration.json (%s), using defaults", exc)
    else:
        _log.warning("%s not found, using defaults", _CALIBRATION_CONFIG)
    return 0, _CALIBRATION_DEFAULT_FACTOR


_zero_offset_raw, CALIBRATION_FACTOR = _load_calibration()
"""float: Converts net raw ADC counts to pounds. Loaded from calibration.json."""


def setup_logging(level: str) -> None:
    """Configures root logger: JournalHandler if available, else StreamHandler.

    Args:
        level: Log level string — "DEBUG", "INFO", "WARNING", "ERROR".
    """
    try:
        from systemd.journal import JournalHandler  # pylint: disable=import-outside-toplevel
        handler = JournalHandler()
    except ImportError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    logging.root.addHandler(handler)
    logging.root.setLevel(getattr(logging, level.upper(), logging.INFO))


class _ReadMetrics:
    """Accumulates ADC read statistics for periodic health reporting.

    Resets every REPORT_INTERVAL seconds to give a rolling per-minute view.
    """

    REPORT_INTERVAL = 60.0
    """float: Seconds between periodic health log emissions."""

    def __init__(self):
        """Initializes all counters and starts the reporting timer."""
        self.reads_ok = 0
        self.reads_none = 0
        self.exceptions = 0
        self._period_start = time.monotonic()

    def reset(self):
        """Resets all counters and restarts the timing period."""
        self.reads_ok = 0
        self.reads_none = 0
        self.exceptions = 0
        self._period_start = time.monotonic()

    def should_report(self) -> bool:
        """Returns True when the reporting interval has elapsed."""
        return (time.monotonic() - self._period_start) >= self.REPORT_INTERVAL


HIGH_WEIGHT_MULTIPLIER = 0.9482
"""float: Correction factor for high-weight linearity."""

TARGET_FPS = 30
"""int: Target frame rate for the main loop."""

# ----------------------------
# Physics Data
# ----------------------------
BODIES_LEFT = [
    ("MERCURY", 0.378, 4879),
    ("VENUS", 0.908, 12104),
    ("MOON", 0.163, 3475),
    ("MARS", 0.378, 6779),
    ("JUPITER", 2.357, 139820),
    ("IO", 0.183, 3643),
]
"""list[tuple]: Left column bodies as (name, gravity_ratio, diameter_km)."""

BODIES_RIGHT = [
    ("SATURN", 0.918, 116460),
    ("TITAN", 0.138, 5150),
    ("URANUS", 0.888, 50724),
    ("NEPTUNE", 1.122, 49244),
    ("PLUTO", 0.061, 2377),
    ("SUN", 27.959, 1391400),
]
"""list[tuple]: Right column bodies as (name, gravity_ratio, diameter_km)."""

# ----------------------------
# Colors
# ----------------------------
COLOR_BG = (10, 12, 22)
COLOR_FG = (245, 245, 245)
COLOR_MUTED = (190, 190, 190)
COLOR_ACCENT = (120, 180, 255)
COLOR_NASA_RED = (252, 61, 33)


# ----------------------------
# Systemd Watchdog
# ----------------------------
class Watchdog:
    """Integrates with systemd's watchdog to reboot on application hang.

    If the systemd service is configured with ``WatchdogSec=``, this class
    sends keep-alive notifications at the required interval. If the main
    loop stops calling ``kick()``, systemd will restart the service (and
    optionally reboot the Pi).

    Attributes:
        enabled: Whether the watchdog is active.
        interval: Time between kicks in seconds.
    """

    def __init__(self):
        """Initializes the watchdog from the WATCHDOG_USEC environment var."""
        usec = os.environ.get("WATCHDOG_USEC")
        if usec:
            self.enabled = True
            # Kick at half the watchdog interval for safety margin
            self.interval = int(usec) / 1_000_000 / 2
        else:
            self.enabled = False
            self.interval = 0
        self._last_kick = 0.0

    def kick(self):
        """Sends a keep-alive notification to systemd if interval has elapsed."""
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_kick >= self.interval:
            self._notify("WATCHDOG=1")
            self._last_kick = now

    def ready(self):
        """Notifies systemd that the service has finished starting up."""
        if self.enabled:
            self._notify("READY=1")

    @staticmethod
    def _notify(message):
        """Sends a notification string to the systemd notify socket.

        Args:
            message: The sd_notify message string (e.g. "WATCHDOG=1").
        """
        addr = os.environ.get("NOTIFY_SOCKET")
        if not addr:
            return
        # pylint: disable=import-outside-toplevel
        import socket
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.connect(addr)
            sock.sendall(message.encode())
        finally:
            sock.close()


# ----------------------------
# Weight Reader Thread
# ----------------------------
@dataclass
class WeightState:
    """Thread-safe container for the latest weight reading.

    Attributes:
        last_lb: The most recent filtered weight reading in pounds.
        connected: Whether the HX711 is responding to read requests.
        is_stable: Whether recent readings have low variance.
        variance: Current reading variance (peak-to-peak) in pounds.
    """

    last_lb: float = 0.0
    connected: bool = False
    is_stable: bool = False
    variance: float = 0.0


class WeightReaderThread(threading.Thread):  # pylint: disable=too-many-instance-attributes
    """Background thread that continuously reads weight from any WeightReader.

    Accepts any ``adc.WeightReader`` backend (HX711 or NAU7802) and runs
    the same filtering pipeline regardless of the underlying hardware.

    The filtering pipeline is:
        1. Spike rejection (delegated to the WeightReader backend)
        2. Median filter (removes single-sample outliers from loose wires)
        3. Rolling average (smooths remaining noise)
        4. Motion-aware CZT (only drifts zero when scale is stable and empty)

    Attributes:
        state: A WeightState instance with the latest reading.
    """

    CZT_THRESHOLD_LB = 4.0
    """float: Weights below this value are candidates for zero tracking."""

    CZT_RATE = 0.05
    """float: Exponential smoothing rate for continuous zero tracking."""

    CZT_STABLE_SECONDS = 2.0
    """float: Seconds of stability required before CZT adjusts the offset."""

    MOTION_THRESHOLD_LB = 0.5
    """float: Peak-to-peak variance threshold for stability detection."""

    BUFFER_SIZE = 7
    """int: Number of readings in the median/average filter buffer."""

    def __init__(self, weight_reader, zero_offset_raw=0):
        """Initializes the weight reader thread.

        Args:
            weight_reader: An ``adc.WeightReader`` instance (HX711 or NAU7802).
            zero_offset_raw: Pre-loaded zero offset from calibration.json.
                If non-zero, the startup tare is skipped and this value is
                used as the initial zero. Defaults to 0 (tare on startup).
        """
        super().__init__(daemon=True)
        self._reader = weight_reader
        self.state = WeightState()
        self._stop_event = threading.Event()
        self._software_offset_raw = zero_offset_raw
        self._needs_tare = zero_offset_raw == 0
        self._buffer = []
        self._stable_since = 0.0
        self._metrics = _ReadMetrics()

    def do_software_tare(self):
        """Flags the reader to capture the next reading as the zero offset."""
        self._needs_tare = True

    def clear_buffer(self):
        """Clears the rolling filter buffer.

        Call this when transitioning from idle to calculation to prevent
        stale near-zero readings from suppressing the first real weight
        readings through the median filter.
        """
        self._buffer.clear()

    def stop(self):
        """Signals the reader thread to stop."""
        self._stop_event.set()

    def run(self):
        """Main loop: reads weight continuously via the injected WeightReader.

        Waits 1 second for hardware settling, then tares (unless a saved
        zero offset was loaded from calibration.json).  Calls
        ``self._reader.close()`` in the finally block regardless of how
        the loop exits.
        """
        time.sleep(1.0)
        if self._needs_tare:
            self.do_software_tare()

        try:
            while not self._stop_event.is_set():
                self._read_cycle()
        finally:
            self._reader.close()

    def _read_cycle(self):
        """Performs a single read-process-update cycle."""
        try:
            raw = self._reader.read_raw()
            if raw is not None:
                self.state.connected = True
                self._metrics.reads_ok += 1
                self._process_reading(raw)
            else:
                # Not ready yet (NAU7802 between cycles) or demo mode
                self._metrics.reads_none += 1
                time.sleep(0.02)
        except Exception as exc:  # pylint: disable=broad-except
            self.state.connected = False
            self._metrics.exceptions += 1
            _log.error("Weight reader error: %s", exc)
            time.sleep(0.5)

        if self._metrics.should_report():
            _log.info(
                "ADC health [60s]: ok=%d none=%d exc=%d variance=%.3f lb connected=%s",
                self._metrics.reads_ok, self._metrics.reads_none,
                self._metrics.exceptions, self.state.variance,
                self.state.connected,
                extra={
                    "READS_OK": self._metrics.reads_ok,
                    "READS_NONE": self._metrics.reads_none,
                    "EXCEPTIONS": self._metrics.exceptions,
                    "VARIANCE_LB": round(self.state.variance, 3),
                },
            )
            self._metrics.reset()

    def _process_reading(self, raw):
        """Processes a raw ADC value into a filtered weight in pounds.

        Applies software tare offset, calibration factor, median filtering,
        motion detection, and continuous zero tracking.

        Args:
            raw: The raw 24-bit signed value from the HX711 (pre-validated).
        """
        if self._needs_tare:
            self._software_offset_raw = raw
            self._needs_tare = False
            self._buffer.clear()
            self._stable_since = 0.0

        net_raw = raw - self._software_offset_raw
        net_lb = (net_raw / CALIBRATION_FACTOR) * HIGH_WEIGHT_MULTIPLIER

        self._buffer.append(net_lb)
        if len(self._buffer) > self.BUFFER_SIZE:
            self._buffer.pop(0)

        # Median filter removes single-sample spikes, then average smooths
        filtered_lb = statistics.median(self._buffer)

        # Motion detection: check peak-to-peak variance
        variance = max(self._buffer) - min(self._buffer)
        is_stable = (len(self._buffer) >= self.BUFFER_SIZE
                     and variance < self.MOTION_THRESHOLD_LB)

        self.state.variance = variance
        self.state.is_stable = is_stable

        # Track how long the scale has been stable
        now = time.time()
        if not is_stable:
            self._stable_since = 0.0
        elif self._stable_since == 0.0:
            self._stable_since = now

        stable_duration = now - self._stable_since if self._stable_since else 0

        # CZT: only adjust zero if stable AND near-zero AND stable long enough
        if (abs(filtered_lb) < self.CZT_THRESHOLD_LB
                and is_stable
                and stable_duration >= self.CZT_STABLE_SECONDS):
            drift = raw - self._software_offset_raw
            self._software_offset_raw += int(drift * self.CZT_RATE)
            self.state.last_lb = 0.0
        else:
            self.state.last_lb = filtered_lb


# ----------------------------
# UI Helpers
# ----------------------------
BASE_DIR = Path(__file__).resolve().parent
FONT_PATH = BASE_DIR / "assets" / "fonts" / "Nasalization Rg.otf"


@dataclass
class UIContext:
    """Bundles pygame display objects to reduce function argument counts.

    Attributes:
        screen: The pygame display surface.
        fonts: Dictionary of named font objects.
        width: Screen width in pixels.
        height: Screen height in pixels.
        cache: Dictionary of pre-rendered static text surfaces.
    """

    screen: pygame.Surface
    fonts: dict
    width: int
    height: int
    cache: dict


def load_font(size_px):
    """Loads the custom font, falling back to Arial if unavailable.

    Args:
        size_px: Font size in pixels.

    Returns:
        A pygame Font object.
    """
    if FONT_PATH.exists():
        return pygame.font.Font(str(FONT_PATH), int(size_px))
    return pygame.font.SysFont("arial", int(size_px))


def draw_centered(screen, font, text, y_pos, color):
    """Renders text horizontally centered on the screen.

    Args:
        screen: The pygame display surface.
        font: The font to render with.
        text: The string to render.
        y_pos: Vertical center position in pixels.
        color: RGB tuple for the text color.
    """
    surf = font.render(text, True, color)
    rect = surf.get_rect(center=(screen.get_width() // 2, int(y_pos)))
    screen.blit(surf, rect)


def blit_centered(screen, surf, y_pos):
    """Blits a pre-rendered surface horizontally centered on the screen.

    Args:
        screen: The pygame display surface.
        surf: A pre-rendered pygame Surface.
        y_pos: Vertical center position in pixels.
    """
    rect = surf.get_rect(center=(screen.get_width() // 2, int(y_pos)))
    screen.blit(surf, rect)


def _build_text_cache(fonts):
    """Pre-renders all static text surfaces to avoid per-frame rendering.

    Planet names, diameters, titles, and other text that never changes
    are rendered once at startup and stored in a dictionary for fast
    blitting during the main loop.

    Args:
        fonts: Dictionary of named font objects.

    Returns:
        Dictionary of pre-rendered pygame Surfaces keyed by usage.
    """
    cache = {}

    # Title text (used in idle and results)
    cache["title_discover"] = fonts["title"].render(
        "DISCOVER YOUR WEIGHT", True, COLOR_NASA_RED)
    cache["title_on"] = fonts["sub"].render(
        "ON OTHER CELESTIAL BODIES", True, COLOR_NASA_RED)

    # Calc screen
    cache["calc_title"] = fonts["title"].render(
        "CALCULATING GRAVITIES...", True, COLOR_NASA_RED)
    cache["calc_sub"] = fonts["cta"].render(
        "PLEASE STAY STILL", True, COLOR_ACCENT)

    # Planet names and diameters
    for name, _, diameter_km in BODIES_LEFT + BODIES_RIGHT:
        cache[f"name_{name}"] = fonts["row"].render(
            name, True, COLOR_FG)
        cache[f"diam_{name}"] = fonts["diam"].render(
            f"DIAMETER: {diameter_km:,} km", True, COLOR_MUTED)

    # Earth label
    cache["earth_label"] = fonts["row"].render("EARTH", True, COLOR_FG)

    # Tare/shutdown messages
    cache["hands_off"] = fonts["cta"].render(
        "HANDS OFF SCALE...", True, COLOR_NASA_RED)
    cache["zeroing"] = fonts["cta"].render(
        "ZEROING SCALE...", True, COLOR_ACCENT)
    cache["shutting_down"] = fonts["cta"].render(
        "SHUTTING DOWN...", True, COLOR_NASA_RED)

    # Disconnected sensor label
    cache["disconnected"] = fonts["debug"].render(
        "SENSOR: DISCONNECTED", True, COLOR_MUTED)

    # Calibration shortcut message
    cache["entering_cal"] = fonts["cta"].render(
        "ENTERING CALIBRATION...", True, COLOR_NASA_RED)

    return cache


# ----------------------------
# UI Screens
# ----------------------------
def draw_idle_screen(ui, live_weight, is_connected, now):
    """Draws the idle screen with pulsing 'step on' call to action.

    Uses pre-rendered surfaces for static text and only renders the
    dynamic weight display and pulsing CTA per frame.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        live_weight: Current weight reading in pounds.
        is_connected: Whether the HX711 sensor is responding.
        now: Current timestamp for animation.
    """
    blit_centered(ui.screen, ui.cache["title_discover"], ui.height * 0.20)
    blit_centered(ui.screen, ui.cache["title_on"], ui.height * 0.28)

    # Pulsing CTA is dynamic — must render per frame
    pulse = 0.5 + 0.5 * math.sin(now * 3)
    pulse_color = tuple(int(c * (0.6 + 0.4 * pulse)) for c in COLOR_ACCENT)
    draw_centered(ui.screen, ui.fonts["cta"], "STEP ON THE SCALE",
                  ui.height * 0.52, pulse_color)

    # Live weight display is dynamic
    if is_connected:
        status = f"LIVE: {live_weight:.1f} LB"
        status_surf = ui.fonts["debug"].render(status, True, COLOR_MUTED)
    else:
        status_surf = ui.cache["disconnected"]
    ui.screen.blit(status_surf, (20, ui.height - 40))


def draw_calc_screen(ui, time_progress, stability_progress):
    """Draws the 'calculating' screen with a blended progress bar.

    The progress bar fills based on both elapsed time (70%) and reading
    stability (30%), giving visual feedback that standing still speeds
    up the lock-in.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        time_progress: Float from 0.0 to 1.0 based on elapsed time.
        stability_progress: Float from 0.0 to 1.0 based on reading variance.
    """
    blit_centered(ui.screen, ui.cache["calc_title"], ui.height * 0.35)
    blit_centered(ui.screen, ui.cache["calc_sub"], ui.height * 0.45)

    # Blended progress: time dominates, stability accelerates
    progress = min(0.7 * time_progress + 0.3 * stability_progress, 1.0)

    # Progress bar
    bar_w = ui.width * 0.5
    bar_h = ui.height * 0.025
    bar_x = (ui.width - bar_w) / 2
    bar_y = ui.height * 0.55

    # Background
    pygame.draw.rect(ui.screen, COLOR_MUTED,
                     (bar_x, bar_y, bar_w, bar_h), 1)
    # Fill
    fill_w = bar_w * progress
    if fill_w > 0:
        pygame.draw.rect(ui.screen, COLOR_ACCENT,
                         (bar_x, bar_y, fill_w, bar_h))


def draw_results_screen(ui, weight_lb):
    """Draws the results screen showing weight on all celestial bodies.

    Uses pre-rendered planet name and diameter surfaces from the cache.
    Only weight values are rendered dynamically per frame.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        weight_lb: Captured Earth weight in pounds.
    """
    blit_centered(ui.screen, ui.cache["title_discover"], ui.height * 0.065)
    blit_centered(ui.screen, ui.cache["title_on"], ui.height * 0.12)

    _draw_earth_weight(ui, weight_lb)
    _draw_body_columns(ui, weight_lb)


def _draw_earth_weight(ui, weight_lb):
    """Draws the centered Earth weight label and value.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        weight_lb: Earth weight in pounds.
    """
    earth_y = ui.height * 0.165
    lbl = ui.cache["earth_label"]
    val = ui.fonts["row"].render(f"{weight_lb:.1f} lb", True, COLOR_FG)
    gap = ui.width * 0.05
    total_w = lbl.get_width() + gap + val.get_width()
    start_x = (ui.width - total_w) // 2
    ui.screen.blit(lbl, (start_x, earth_y))
    ui.screen.blit(val, (start_x + lbl.get_width() + gap, earth_y))


def _draw_body_columns(ui, weight_lb):
    """Draws the two-column layout of celestial body weights.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        weight_lb: Earth weight in pounds for gravity ratio calculation.
    """
    col_gap = ui.width * 0.18
    side = ui.width * 0.08
    col_w = (ui.width - 2 * side - col_gap) / 2
    left_x = side
    right_x = left_x + col_w + col_gap
    top_y = ui.height * 0.285
    row_h = ui.height * 0.095
    val_off = col_w * 0.56

    curr_y = top_y
    for name, factor, _ in BODIES_LEFT:
        _draw_body_row(ui, left_x, curr_y, val_off, name, factor, weight_lb)
        curr_y += row_h

    curr_y = top_y
    for name, factor, _ in BODIES_RIGHT:
        _draw_body_row(ui, right_x, curr_y, val_off, name, factor, weight_lb)
        curr_y += row_h


def _draw_body_row(ui, x_pos, y_pos, val_off,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                   name, factor, weight_lb):
    """Draws a single celestial body row with name, weight, and diameter.

    Planet name and diameter surfaces are pulled from the pre-rendered
    cache. Only the weight value is rendered dynamically.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        x_pos: Horizontal position in pixels.
        y_pos: Vertical position in pixels.
        val_off: Horizontal offset for the weight value column.
        name: Name of the celestial body (used as cache key).
        factor: Gravity ratio relative to Earth.
        weight_lb: Earth weight in pounds.
    """
    body_weight = weight_lb * factor
    weight_surf = ui.fonts["row"].render(f"{body_weight:.1f} lb", True,
                                         COLOR_FG)
    ui.screen.blit(ui.cache[f"name_{name}"], (x_pos, y_pos))
    ui.screen.blit(weight_surf, (x_pos + val_off, y_pos))
    ui.screen.blit(ui.cache[f"diam_{name}"],
                   (x_pos, y_pos + ui.fonts["row"].get_height() * 0.88))


# ----------------------------
# Button Handling
# ----------------------------
def handle_shutdown(ui):
    """Displays shutdown message and halts the system.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
    """
    ui.screen.fill(COLOR_BG)
    blit_centered(ui.screen, ui.cache["shutting_down"], ui.height / 2)
    pygame.display.flip()
    time.sleep(2)
    subprocess.call(["sudo", "systemctl", "poweroff"])
    sys.exit()


# ----------------------------
# State Machine
# ----------------------------
STATE_IDLE = "IDLE"
STATE_CALC = "CALC"
STATE_RESULTS = "RESULTS"
STATE_SHUTDOWN = "SHUTDOWN"


@dataclass
class ScaleContext:  # pylint: disable=too-many-instance-attributes
    """Mutable state for the scale's state machine.

    Attributes:
        state: Current UI state (STATE_IDLE, STATE_CALC, STATE_RESULTS,
            or STATE_SHUTDOWN).
        state_start: Timestamp when the current state began.
        captured_weight: The weight captured at the start of calculation.
        step_off_timer: Timestamp when user stepped off during results.
        btn_press_start: Timestamp when the button was first pressed.
        prev_state: Previous state for dirty-rect change detection.
        prev_weight_str: Previous weight string for change detection.
    """

    state: str = STATE_IDLE
    state_start: float = 0.0
    captured_weight: float = 0.0
    step_off_timer: float = 0.0
    btn_press_start: float = 0.0
    prev_state: str = ""
    prev_weight_str: str = ""
    tare_phase: str = ""
    """Current tare animation phase: '' | 'hands_off' | 'zeroing'."""
    tare_phase_start: float = 0.0
    """Timestamp when the current tare phase began."""


# ----------------------------
# Main Loop Helpers
# ----------------------------
def _handle_events(reader):
    """Processes pygame events, handling quit and escape key.

    Args:
        reader: The active weight reader to stop on exit.
    """
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            reader.stop()
            sys.exit()
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            reader.stop()
            sys.exit()


_CAL_PRESS_COUNT = 5
"""int: Number of rapid button presses required to enter calibration mode."""

_CAL_PRESS_WINDOW = 3.0
"""float: Time window in seconds for the rapid-press calibration shortcut."""

CALIBRATION_SCRIPT = BASE_DIR / "calibrate.py"
"""Path: On-screen calibration script launched by the 5-press shortcut."""

_TARE_WAIT_SEC = 2.0
"""float: Seconds to display 'HANDS OFF' before zeroing during tare."""

_TARE_ZERO_SEC = 1.0
"""float: Seconds to display 'ZEROING' after the software tare command."""


def _handle_button(ui, reader, maint_btn, ctx, now,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                   btn_press_times, adc_flag):
    """Processes maintenance button presses for tare, shutdown, and calibration.

    A short press (<5 s) triggers a software tare.  Holding for 10 s+
    triggers safe shutdown.  Five presses within 3 seconds launches the
    on-screen calibration tool.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        reader: The active WeightReaderThread for tare commands.
        maint_btn: A gpiozero Button instance, or None if unavailable.
        ctx: The current ScaleContext.
        now: Current timestamp.
        btn_press_times: Mutable list of recent button-release timestamps,
            used for calibration rapid-press detection.
        adc_flag: The ``--adc`` argument string passed at startup
            (``"hx711"`` or ``"nau7802"``), forwarded to calibrate.py.
    """
    if not maint_btn:
        return

    if maint_btn.is_pressed:
        if ctx.btn_press_start == 0.0:
            ctx.btn_press_start = now
            _log.debug("Button pressed")
        if (ctx.state != STATE_SHUTDOWN
                and (now - ctx.btn_press_start) > SHUTDOWN_HOLD_SEC):
            _log.info("Shutdown triggered (held %.1f s)",
                      now - ctx.btn_press_start)
            ctx.state = STATE_SHUTDOWN
    elif ctx.btn_press_start > 0.0:
        press_duration = now - ctx.btn_press_start
        ctx.btn_press_start = 0.0
        _log.debug("Button released (duration=%.2f s)", press_duration)

        if ctx.state != STATE_SHUTDOWN and press_duration < 5.0:
            # Track this release for calibration rapid-press detection
            btn_press_times.append(now)
            btn_press_times[:] = [t for t in btn_press_times
                                   if now - t < _CAL_PRESS_WINDOW]

            if len(btn_press_times) >= _CAL_PRESS_COUNT:
                _log.info("Calibration shortcut triggered (5 presses in %.1f s)",
                          _CAL_PRESS_WINDOW)
                btn_press_times.clear()
                ui.screen.fill(COLOR_BG)
                blit_centered(ui.screen, ui.cache["entering_cal"],
                               ui.height / 2)
                pygame.display.flip()
                reader.stop()
                reader.join(timeout=2)
                maint_btn.close()
                subprocess.run(
                    [sys.executable, str(CALIBRATION_SCRIPT),
                     "--adc", adc_flag],
                    check=False,
                )
                sys.exit(0)

            if ctx.tare_phase == "":
                _log.info("Tare triggered (press_duration=%.2f s)",
                          press_duration)
                ctx.tare_phase = "hands_off"
                ctx.tare_phase_start = now
                ctx.state = STATE_IDLE
                ctx.captured_weight = 0.0


def _advance_tare(ctx, reader, now):
    """Advances the non-blocking tare animation sequence.

    Called every frame while ctx.tare_phase is non-empty.  Transitions
    through two phases:

        ``"hands_off"`` — displays 'HANDS OFF SCALE...' for _TARE_WAIT_SEC,
                          then triggers the software tare and moves to
                          ``"zeroing"``.
        ``"zeroing"``   — displays 'ZEROING SCALE...' for _TARE_ZERO_SEC,
                          then clears tare_phase to resume normal rendering.

    Args:
        ctx: The current ScaleContext.
        reader: The WeightReaderThread to tare once the wait expires.
        now: Current timestamp.
    """
    elapsed = now - ctx.tare_phase_start
    if ctx.tare_phase == "hands_off" and elapsed >= _TARE_WAIT_SEC:
        reader.do_software_tare()
        ctx.tare_phase = "zeroing"
        ctx.tare_phase_start = now
    elif ctx.tare_phase == "zeroing" and elapsed >= _TARE_ZERO_SEC:
        ctx.tare_phase = ""


def _update_state(ctx, reader, now):
    """Advances the state machine based on current weight and timing.

    If a tare sequence is in progress, advances that and returns early
    to suppress weight-based state transitions.

    Args:
        ctx: The current ScaleContext.
        reader: The HX711WeightReader instance.
        now: Current timestamp.
    """
    if ctx.tare_phase:
        _advance_tare(ctx, reader, now)
        return

    if ctx.state == STATE_IDLE:
        _update_idle(ctx, reader, now)
    elif ctx.state == STATE_CALC:
        _update_calc(ctx, reader.state, now)
    elif ctx.state == STATE_RESULTS:
        _update_results(ctx, reader.state.last_lb, now)


def _update_idle(ctx, reader, now):
    """Handles state transitions from the idle state.

    Clears the reader's filter buffer on transition to CALC to prevent
    stale near-zero readings from suppressing the initial weight through
    the median filter.

    Args:
        ctx: The current ScaleContext.
        reader: The HX711WeightReader instance.
        now: Current timestamp.
    """
    ctx.step_off_timer = 0.0
    live_weight = reader.state.last_lb
    if live_weight > TRIGGER_THRESHOLD_LB:
        reader.clear_buffer()
        ctx.captured_weight = live_weight
        ctx.state = STATE_CALC
        ctx.state_start = now
        _log.info("IDLE→CALC weight=%.1f lb", live_weight)


def _update_calc(ctx, reader_state, now):
    """Handles state transitions from the calculation state.

    Transitions to RESULTS when readings stabilize (low variance) or
    when the maximum calculation time elapses — whichever comes first.

    Args:
        ctx: The current ScaleContext.
        reader_state: The WeightState from the reader thread.
        now: Current timestamp.
    """
    live_weight = reader_state.last_lb

    if live_weight > TRIGGER_THRESHOLD_LB:
        ctx.captured_weight = live_weight

    if live_weight < (TRIGGER_THRESHOLD_LB - 1.0):
        elapsed = now - ctx.state_start
        _log.info("CALC→IDLE weight dropped (%.1f lb, elapsed=%.1f s)",
                  live_weight, elapsed)
        ctx.state = STATE_IDLE
        return

    elapsed = now - ctx.state_start

    if (reader_state.is_stable
            and reader_state.variance < CALC_VARIANCE_THRESHOLD
            and elapsed > 1.0):
        _log.info(
            "CALC→RESULTS stable lock-in at %.1f lb "
            "(variance=%.3f lb, elapsed=%.1f s)",
            ctx.captured_weight, reader_state.variance, elapsed,
        )
        ctx.state = STATE_RESULTS
        ctx.state_start = now
    elif elapsed > CALC_SECONDS:
        _log.info("CALC→RESULTS timeout at %.1f lb (elapsed=%.1f s)",
                  ctx.captured_weight, elapsed)
        ctx.state = STATE_RESULTS
        ctx.state_start = now


def _update_results(ctx, live_weight, now):
    """Handles state transitions from the results state.

    Returns to idle after the user steps off (with grace period) or
    after the results timeout expires.

    Args:
        ctx: The current ScaleContext.
        live_weight: Current smoothed weight reading in pounds.
        now: Current timestamp.
    """
    if live_weight < (TRIGGER_THRESHOLD_LB - 1.0):
        if ctx.step_off_timer == 0.0:
            ctx.step_off_timer = now
        if (now - ctx.step_off_timer) > STEP_OFF_DELAY:
            _log.info("RESULTS→IDLE user stepped off (time_in_results=%.1f s)",
                      ctx.step_off_timer - ctx.state_start)
            ctx.state = STATE_IDLE
    else:
        ctx.step_off_timer = 0.0
    if now - ctx.state_start > RESULTS_TIMEOUT:
        _log.info("RESULTS→IDLE timeout (%.0f s)", RESULTS_TIMEOUT)
        ctx.state = STATE_IDLE


def _needs_redraw(ctx):
    """Determines whether the screen needs a full redraw this frame.

    Avoids redrawing every frame when nothing has changed, reducing
    CPU load on the Pi Zero's single core.

    Args:
        ctx: The current ScaleContext.

    Returns:
        True if the screen should be redrawn this frame.
    """
    # Always redraw on state change
    if ctx.state != ctx.prev_state:
        ctx.prev_state = ctx.state
        ctx.prev_weight_str = ""
        return True

    # Idle screen: redraw for pulsing animation and weight updates
    if ctx.state == STATE_IDLE:
        return True

    # Calc screen: redraw for progress bar animation
    if ctx.state == STATE_CALC:
        return True

    # Results screen: only redraw if weight display changed
    if ctx.state == STATE_RESULTS:
        weight_str = f"{ctx.captured_weight:.1f}"
        if weight_str != ctx.prev_weight_str:
            ctx.prev_weight_str = weight_str
            return True
        return False

    return True


def _draw(ui, ctx, reader_state, now):
    """Dispatches drawing to the appropriate screen function.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        ctx: The current ScaleContext.
        reader_state: The WeightState from the reader thread.
        now: Current timestamp for animations.
    """
    ui.screen.fill(COLOR_BG)

    if ctx.tare_phase == "hands_off":
        blit_centered(ui.screen, ui.cache["hands_off"], ui.height / 2)
    elif ctx.tare_phase == "zeroing":
        blit_centered(ui.screen, ui.cache["zeroing"], ui.height / 2)
    elif ctx.state == STATE_IDLE:
        draw_idle_screen(ui, reader_state.last_lb,
                         reader_state.connected, now)
    elif ctx.state == STATE_CALC:
        elapsed = now - ctx.state_start
        time_progress = min(elapsed / CALC_SECONDS, 1.0)

        # Map variance to stability with a wide range to prevent flicker.
        # 0 variance = 1.0 (perfectly still), 2.0+ lbs = 0.0 (very shaky).
        # Using 2x the lock-in threshold avoids jitter near the boundary.
        stability_progress = max(0.0, 1.0 - (reader_state.variance / 2.0))

        draw_calc_screen(ui, time_progress, stability_progress)
    elif ctx.state == STATE_RESULTS:
        draw_results_screen(ui, ctx.captured_weight)


# ----------------------------
# Main
# ----------------------------
def main():  # pylint: disable=too-many-locals,too-many-statements
    """Entry point for the Celestial Scale application.

    Initializes pygame, the selected ADC backend, systemd watchdog, and
    runs the main event loop with state machine logic for idle,
    calculation, results, and shutdown screens.

    Command-line args:
        --adc {hx711,nau7802}: ADC backend to use. Defaults to hx711.
        --log-level {DEBUG,INFO,WARNING,ERROR}: Logging verbosity. Defaults to INFO.
    """
    parser = argparse.ArgumentParser(description="Celestial Scale Kiosk")
    parser.add_argument("--adc", choices=["hx711", "nau7802"],
                        default="hx711",
                        help="ADC backend: hx711 (pigpio) or nau7802 (I2C)")
    parser.add_argument("--log-level",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        default="INFO",
                        help="Logging verbosity (default: INFO)")
    args = parser.parse_args()
    setup_logging(args.log_level)

    watchdog = Watchdog()

    # Set highest process priority for timing-sensitive GPIO.
    # The systemd service handles this via Nice=-20, but this covers
    # manual runs during development and debugging.
    if sys.platform == "linux":
        try:
            os.nice(-20)
        except PermissionError:
            pass  # Handled by systemd Nice=-20 in production

    pygame.display.init()
    pygame.font.init()

    flags = 0 if WINDOWED_DEV else pygame.FULLSCREEN
    if WINDOWED_DEV:
        screen = pygame.display.set_mode((1280, 720), flags)
    else:
        screen = pygame.display.set_mode((0, 0), flags)
    pygame.mouse.set_visible(WINDOWED_DEV)
    width, height = screen.get_size()

    _log.info("ADC backend: %s", args.adc)
    _log.info("Calibration: zero_offset=%d factor=%.4f (file=%s)",
              _zero_offset_raw, CALIBRATION_FACTOR, _CALIBRATION_CONFIG)
    _log.info("Display: %dx%d watchdog=%s", width, height,
              f"enabled ({watchdog.interval:.1f}s kick)"
              if watchdog.enabled else "disabled")

    fonts = {
        "title": load_font(height * 0.07),
        "sub": load_font(height * 0.05),
        "cta": load_font(height * 0.10),
        "row": load_font(height * 0.052),
        "diam": load_font(height * 0.034),
        "debug": load_font(height * 0.03),
    }

    cache = _build_text_cache(fonts)
    ui = UIContext(screen=screen, fonts=fonts, width=width,
                   height=height, cache=cache)

    if args.adc == "hx711":
        adc_backend = HX711WeightReader(HX711_DOUT_PIN, HX711_SCK_PIN)
    else:
        adc_backend = NAU7802WeightReader()

    reader = WeightReaderThread(adc_backend, zero_offset_raw=_zero_offset_raw)
    reader.start()

    maint_btn = None
    if Button:
        maint_btn = Button(BUTTON_PIN_BCM, pull_up=True)

    ctx = ScaleContext()
    clock = pygame.time.Clock()
    btn_press_times = []

    watchdog.ready()

    try:
        while True:
            now = time.time()
            clock.tick(TARGET_FPS)
            watchdog.kick()

            _handle_events(reader)
            _handle_button(ui, reader, maint_btn, ctx, now,
                           btn_press_times, args.adc)

            if ctx.state == STATE_SHUTDOWN:
                handle_shutdown(ui)

            _update_state(ctx, reader, now)

            if _needs_redraw(ctx):
                _draw(ui, ctx, reader.state, now)
                pygame.display.flip()
    finally:
        reader.stop()
        reader.join(timeout=2)


if __name__ == "__main__":
    main()
