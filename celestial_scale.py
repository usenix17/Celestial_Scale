#!/usr/bin/env python3
# pylint: disable=too-many-lines
"""Celestial Scale - Planetary Weight Display Kiosk.

Reads weight from an HX711 load cell amplifier via pigpio and displays
the user's weight on various celestial bodies through a pygame UI on
an HDMI-connected screen.

Hardware:
    - HX711 load cell amplifier on GPIO 5 (DOUT) and GPIO 6 (SCK)
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
    - Button: <5s press = Tare, 10s+ hold = Safe shutdown

Requires:
    - pigpiod running (systemctl enable pigpiod)
    - python3-pygame
"""

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

try:
    import pigpio
except ImportError:
    pigpio = None

try:
    from gpiozero import Button
except ImportError:
    Button = None

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

CALIBRATION_FACTOR = 420.0
"""float: Converts raw HX711 counts to pounds. Adjust with a known weight."""

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
# HX711 Driver (pigpio)
# ----------------------------
def _busy_wait_us(microseconds):
    """Busy-waits for the specified number of microseconds.

    Uses a spin loop instead of ``time.sleep()`` for sub-millisecond
    accuracy. ``time.sleep()`` on Linux has ~1ms minimum granularity
    due to kernel scheduling, which is too coarse for the HX711's
    timing requirements.

    Args:
        microseconds: Number of microseconds to wait.
    """
    end = time.perf_counter() + (microseconds / 1_000_000)
    while time.perf_counter() < end:
        pass


class HX711:
    """Driver for the HX711 24-bit ADC using pigpio for hardware-timed GPIO.

    The HX711 uses a proprietary serial protocol where data is clocked out
    one bit at a time. This driver uses pigpio's DMA-based GPIO access to
    maintain consistent timing, avoiding corruption from Linux kernel
    preemption.

    Clock pulses use busy-wait spin loops rather than ``time.sleep()`` for
    accurate microsecond timing on the Pi Zero's single-core ARM.

    Attributes:
        pi: A pigpio.pi() instance for GPIO access.
        dout_pin: BCM pin number for HX711 data output.
        sck_pin: BCM pin number for HX711 serial clock.
    """

    GAIN_PULSES = {128: 1, 64: 3, 32: 2}
    """dict: Maps gain values to the number of extra clock pulses required."""

    CLOCK_PULSE_US = 10
    """int: Clock pulse width in microseconds."""

    READY_TIMEOUT = 2.0
    """float: Maximum time to wait for HX711 ready signal in seconds."""

    # Raw values at or beyond this magnitude are almost certainly garbage
    SPIKE_THRESHOLD = 8_000_000
    """int: Raw ADC values beyond this are rejected as noise spikes."""

    def __init__(self, pi, dout_pin, sck_pin, gain=128):
        """Initializes the HX711 driver and configures GPIO pins.

        Args:
            pi: A connected pigpio.pi() instance.
            dout_pin: BCM GPIO pin connected to HX711 DOUT.
            sck_pin: BCM GPIO pin connected to HX711 SCK.
            gain: Amplifier gain setting. Valid values are 128 (Channel A),
                64 (Channel A), or 32 (Channel B). Defaults to 128.
        """
        self.pi = pi
        self.dout_pin = dout_pin
        self.sck_pin = sck_pin
        self._gain_pulses = self.GAIN_PULSES.get(gain, 1)

        self.pi.set_mode(self.dout_pin, pigpio.INPUT)
        self.pi.set_mode(self.sck_pin, pigpio.OUTPUT)
        self.pi.write(self.sck_pin, 0)

    def is_ready(self):
        """Checks whether the HX711 has a new reading available.

        Returns:
            True if DOUT is low, indicating data is ready to be clocked out.
        """
        return self.pi.read(self.dout_pin) == 0

    def read_raw(self):
        """Reads one raw 24-bit signed value from the HX711.

        Waits for the HX711 to signal data ready (DOUT low), then clocks
        out 24 bits of data using busy-wait timing, plus additional pulses
        to set the gain for the next conversion.

        Returns:
            A signed integer representing the raw ADC value, or None if
            the reading is a garbage spike.

        Raises:
            TimeoutError: If the HX711 does not become ready within
                READY_TIMEOUT seconds.
        """
        deadline = time.time() + self.READY_TIMEOUT
        while not self.is_ready():
            if time.time() > deadline:
                raise TimeoutError("HX711 not responding")
            time.sleep(0.001)

        raw = 0
        for _ in range(24):
            self.pi.write(self.sck_pin, 1)
            _busy_wait_us(self.CLOCK_PULSE_US)
            raw = (raw << 1) | self.pi.read(self.dout_pin)
            self.pi.write(self.sck_pin, 0)
            _busy_wait_us(self.CLOCK_PULSE_US)

        for _ in range(self._gain_pulses):
            self.pi.write(self.sck_pin, 1)
            _busy_wait_us(self.CLOCK_PULSE_US)
            self.pi.write(self.sck_pin, 0)
            _busy_wait_us(self.CLOCK_PULSE_US)

        if raw & 0x800000:
            raw -= 0x1000000

        # Reject obvious garbage readings (spikes from loose wires)
        if raw == 0 or raw == -1 or abs(raw) > self.SPIKE_THRESHOLD:
            return None

        return raw

    def power_down(self):
        """Powers down the HX711 by holding SCK high for >60 microseconds."""
        self.pi.write(self.sck_pin, 1)
        time.sleep(0.0001)

    def power_up(self):
        """Wakes the HX711 by pulling SCK low."""
        self.pi.write(self.sck_pin, 0)


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


class HX711WeightReader(threading.Thread):  # pylint: disable=too-many-instance-attributes
    """Background thread that continuously reads weight from an HX711.

    Handles software tare, median-filtered spike rejection, rolling
    average smoothing, motion-aware continuous zero tracking (CZT), and
    stability detection.

    The filtering pipeline is:
        1. Spike rejection (discard raw values near 0, -1, or full-scale)
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

    def __init__(self, dout_pin=HX711_DOUT_PIN, sck_pin=HX711_SCK_PIN):
        """Initializes the weight reader thread.

        Args:
            dout_pin: BCM GPIO pin for HX711 data output.
            sck_pin: BCM GPIO pin for HX711 serial clock.
        """
        super().__init__(daemon=True)
        self.dout_pin = dout_pin
        self.sck_pin = sck_pin
        self.state = WeightState()
        self._stop_event = threading.Event()
        self._software_offset_raw = 0
        self._needs_tare = False
        self._buffer = []
        self._stable_since = 0.0

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
        """Main loop: connects to pigpiod and reads weight continuously.

        Falls back to demo mode if pigpio is unavailable or pigpiod is
        not running. Automatically tares on startup after a 1-second
        settling period.
        """
        if not pigpio:
            print("pigpio not available - running in demo mode")
            return

        pi = pigpio.pi()
        if not pi.connected:
            print("Could not connect to pigpiod - is it running?")
            return

        try:
            hx = HX711(pi, self.dout_pin, self.sck_pin, gain=128)
            time.sleep(1.0)
            self.do_software_tare()

            while not self._stop_event.is_set():
                self._read_cycle(hx)
        finally:
            pi.stop()

    def _read_cycle(self, hx):
        """Performs a single read-process-update cycle.

        Args:
            hx: An initialized HX711 driver instance.
        """
        try:
            raw = hx.read_raw()
            self.state.connected = True
            if raw is not None:
                self._process_reading(raw)
        except TimeoutError:
            self.state.connected = False
            time.sleep(0.5)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"HX711 read error: {exc}")
            time.sleep(0.1)

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
def handle_tare(ui, reader):
    """Displays tare prompts and triggers a software tare.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        reader: The active HX711WeightReader instance.
    """
    ui.screen.fill(COLOR_BG)
    blit_centered(ui.screen, ui.cache["hands_off"], ui.height / 2)
    pygame.display.flip()
    time.sleep(2.0)

    reader.do_software_tare()

    ui.screen.fill(COLOR_BG)
    blit_centered(ui.screen, ui.cache["zeroing"], ui.height / 2)
    pygame.display.flip()
    time.sleep(1.0)


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
class ScaleContext:
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


def _handle_button(ui, reader, maint_btn, ctx, now):
    """Processes maintenance button presses for tare and shutdown.

    Args:
        ui: The UIContext with screen, fonts, dimensions, and cache.
        reader: The active weight reader for tare commands.
        maint_btn: A gpiozero Button instance, or None if unavailable.
        ctx: The current ScaleContext.
        now: Current timestamp.
    """
    if not maint_btn:
        return

    if maint_btn.is_pressed:
        if ctx.btn_press_start == 0.0:
            ctx.btn_press_start = now
        if (now - ctx.btn_press_start) > SHUTDOWN_HOLD_SEC:
            ctx.state = STATE_SHUTDOWN
    elif ctx.btn_press_start > 0.0:
        press_duration = now - ctx.btn_press_start
        ctx.btn_press_start = 0.0

        if ctx.state != STATE_SHUTDOWN and press_duration < 5.0:
            handle_tare(ui, reader)
            ctx.state = STATE_IDLE
            ctx.captured_weight = 0.0


def _update_state(ctx, reader, now):
    """Advances the state machine based on current weight and timing.

    Args:
        ctx: The current ScaleContext.
        reader: The HX711WeightReader instance.
        now: Current timestamp.
    """
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
        ctx.state = STATE_IDLE
        return

    elapsed = now - ctx.state_start

    if (reader_state.is_stable
            and reader_state.variance < CALC_VARIANCE_THRESHOLD
            and elapsed > 1.0):
        ctx.state = STATE_RESULTS
        ctx.state_start = now
    elif elapsed > CALC_SECONDS:
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
            ctx.state = STATE_IDLE
    else:
        ctx.step_off_timer = 0.0
    if now - ctx.state_start > RESULTS_TIMEOUT:
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

    if ctx.state == STATE_IDLE:
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
def main():
    """Entry point for the Celestial Scale application.

    Initializes pygame, hardware readers, systemd watchdog, and runs
    the main event loop with state machine logic for idle, calculation,
    results, and shutdown screens.
    """
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

    reader = HX711WeightReader(HX711_DOUT_PIN, HX711_SCK_PIN)
    reader.start()

    maint_btn = None
    if Button:
        maint_btn = Button(BUTTON_PIN_BCM, pull_up=True)

    ctx = ScaleContext()
    clock = pygame.time.Clock()

    watchdog.ready()

    try:
        while True:
            now = time.time()
            clock.tick(TARGET_FPS)
            watchdog.kick()

            _handle_events(reader)
            _handle_button(ui, reader, maint_btn, ctx, now)

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
