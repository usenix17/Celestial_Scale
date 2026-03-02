#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""Celestial Scale On-Screen Calibration Tool.

Runs as a full-screen pygame application on the kiosk HDMI display.
Guides the operator through a two-point calibration procedure using
only the maintenance button (GPIO 18) — no keyboard or terminal needed.

Procedure:
    1. Remove all weight → press button to capture zero offset.
    2. Place known weight → button captured automatically.
    3. Enter the known weight in lbs using the button as a digit selector.
    4. Calibration factor and zero offset are written to
       /etc/celestial-scale/calibration.json.
    5. The main celestial_scale service restarts automatically
       (systemd Restart=always) and picks up the new values.

Button interaction:
    Short press (<1 s): increment the current digit (0→1→…→9→0)
    Long press  (≥1 s): advance to the next digit position;
                        long press on the last digit confirms the entry.

Requires:
    - python3-pygame
    - python3-gpiozero
    - adc.py (same directory)
"""

import argparse
import json
import logging
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

import pygame

from adc import HX711WeightReader, NAU7802WeightReader, WeightReader

_log = logging.getLogger(__name__)

try:
    from gpiozero import Button
except ImportError:
    Button = None  # type: ignore[assignment,misc]

# ----------------------------
# Config
# ----------------------------
_CONFIG_DIR = Path("/etc/celestial-scale")
_CALIBRATION_CONFIG = _CONFIG_DIR / "calibration.json"

SAMPLE_COUNT = 20
"""int: Number of ADC readings captured per calibration step."""

BUTTON_PIN_BCM = 18
"""int: BCM GPIO pin for the maintenance button (Physical Pin 12)."""

LONG_PRESS_SEC = 1.0
"""float: Press duration in seconds that counts as a long press."""

RESTART_DELAY = 5.0
"""float: Seconds to display the success screen before exiting."""

ERROR_DISPLAY_SEC = 10.0
"""float: Seconds to display an error message before exiting."""

TARGET_FPS = 30
"""int: Target frame rate for the calibration UI loop."""

# ----------------------------
# Calibration step constants
# ----------------------------
STEP_CLEAR = "CLEAR"
"""Prompt: remove all weight, press button to continue."""

STEP_ZERO = "ZERO"
"""Sampling: capture zero-load readings with progress bar."""

STEP_PLACE = "PLACE"
"""Prompt: place known weight, press button to continue."""

STEP_LOAD = "LOAD"
"""Sampling: capture loaded readings with progress bar."""

STEP_ENTER = "ENTER"
"""Numeric entry: button-based digit selector for known weight."""

STEP_DONE = "DONE"
"""Display: calibration complete, countdown to exit."""

STEP_ERROR = "ERROR"
"""Display: error message, auto-exit after timeout."""

# ----------------------------
# Colors (match celestial_scale.py)
# ----------------------------
COLOR_BG = (10, 12, 22)
COLOR_FG = (245, 245, 245)
COLOR_MUTED = (190, 190, 190)
COLOR_ACCENT = (120, 180, 255)
COLOR_NASA_RED = (252, 61, 33)

BASE_DIR = Path(__file__).resolve().parent
FONT_PATH = BASE_DIR / "assets" / "fonts" / "Nasalization Rg.otf"


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


# ----------------------------
# Helpers
# ----------------------------
def load_font(size_px):
    """Loads the custom kiosk font, falling back to Arial if unavailable.

    Args:
        size_px: Font size in pixels.

    Returns:
        A pygame Font object.
    """
    if FONT_PATH.exists():
        return pygame.font.Font(str(FONT_PATH), int(size_px))
    return pygame.font.SysFont("arial", int(size_px))


def draw_centered(screen, font, text, y_pos, color):
    """Renders text horizontally centred on the screen.

    Args:
        screen: The pygame display surface.
        font: The pygame Font to render with.
        text: The string to render.
        y_pos: Vertical centre position in pixels.
        color: RGB tuple for the text colour.
    """
    surf = font.render(text, True, color)
    rect = surf.get_rect(center=(screen.get_width() // 2, int(y_pos)))
    screen.blit(surf, rect)


def draw_progress_bar(screen, progress, width, height):
    """Draws a horizontal progress bar centred on the screen.

    Args:
        screen: The pygame display surface.
        progress: Fill fraction from 0.0 to 1.0.
        width: Screen width in pixels.
        height: Screen height in pixels.
    """
    bar_w = width * 0.5
    bar_h = height * 0.025
    bar_x = (width - bar_w) / 2
    bar_y = height * 0.6
    pygame.draw.rect(screen, COLOR_MUTED, (bar_x, bar_y, bar_w, bar_h), 1)
    fill_w = bar_w * min(progress, 1.0)
    if fill_w > 0:
        pygame.draw.rect(screen, COLOR_ACCENT, (bar_x, bar_y, fill_w, bar_h))


def write_calibration_json(zero_offset, calibration_factor):
    """Writes calibration values to /etc/celestial-scale/calibration.json.

    Creates the directory if it does not exist.  The file is read by
    celestial_scale.py on startup.

    Args:
        zero_offset: Raw ADC count with no weight on the scale (int).
        calibration_factor: Counts-per-pound conversion factor (float).
    """
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "_note": ("Generated by calibrate.py. "
                  "Run calibrate.py to recalibrate."),
        "zero_offset": int(zero_offset),
        "calibration_factor": round(float(calibration_factor), 4),
    }
    try:
        _CALIBRATION_CONFIG.write_text(json.dumps(data, indent=2),
                                       encoding="utf-8")
        _log.info("Calibration written: zero_offset=%d factor=%.4f path=%s",
                  int(zero_offset), round(float(calibration_factor), 4),
                  _CALIBRATION_CONFIG)
    except OSError as exc:
        _log.error("Failed to write calibration: %s", exc)
        raise


# ----------------------------
# Digit entry state
# ----------------------------
class DigitEntry:  # pylint: disable=too-few-public-methods
    """Mutable state for the button-driven numeric weight entry.

    Attributes:
        digits: List of 5 single digit ints: [d0, d1, d2, d3, d4]
            representing the value ``d0d1d2d3.d4`` lbs.
        active: Index of the digit currently being incremented (0–4).
        btn_down_time: Timestamp of the current button press start,
            or 0.0 if the button is not held.
        confirmed: True once the operator long-presses on digit 4.
    """

    NUM_DIGITS = 5
    """int: Total digit positions (4 integer + 1 decimal)."""

    def __init__(self):
        """Initialises all digits to zero."""
        self.digits = [0] * self.NUM_DIGITS
        self.active = 0
        self.btn_down_time = 0.0
        self.confirmed = False

    @property
    def weight_lbs(self) -> float:
        """float: Current value of the digit entry as a float in lbs."""
        d = self.digits
        return float(f"{d[0]}{d[1]}{d[2]}{d[3]}.{d[4]}")

    def draw(self, screen, fonts, width, height):  # pylint: disable=too-many-locals
        """Renders the digit selector on screen.

        Integer digits are drawn left of the decimal point, the decimal
        digit to its right.  The active digit is highlighted in accent
        colour; others are rendered in the foreground colour.

        Args:
            screen: The pygame display surface.
            fonts: Dictionary of named font objects.
            width: Screen width in pixels.
            height: Screen height in pixels.
        """
        digit_font = fonts["cta"]
        char_w = digit_font.size("0")[0]
        dot_w = digit_font.size(".")[0]
        gap = int(char_w * 0.2)

        # Total width: 4 int digits + dot + 1 dec digit + gaps
        total_w = 4 * char_w + dot_w + char_w + 5 * gap
        start_x = (width - total_w) // 2
        y_centre = int(height * 0.52)

        x = start_x
        for i, digit in enumerate(self.digits):
            if i == 4:
                # Draw decimal point first
                dot_surf = digit_font.render(".", True, COLOR_MUTED)
                dot_rect = dot_surf.get_rect(midleft=(x, y_centre))
                screen.blit(dot_surf, dot_rect)
                x += dot_w + gap

            color = COLOR_ACCENT if i == self.active else COLOR_FG
            d_surf = digit_font.render(str(digit), True, color)
            d_rect = d_surf.get_rect(midleft=(x, y_centre))
            screen.blit(d_surf, d_rect)
            x += char_w + gap


# ----------------------------
# Main calibration loop
# ----------------------------
def _confirm_entry(entry, zero_offset, load_readings):  # pylint: disable=too-many-return-statements
    """Validates the entered weight and computes the calibration factor.

    Called once the operator confirms their weight entry via long press.

    Args:
        entry: The completed DigitEntry with the entered weight.
        zero_offset: Median raw ADC value from the zero-load capture.
        load_readings: List of raw ADC values from the loaded capture.

    Returns:
        tuple[str, str, float]: ``(next_step, error_msg, cal_factor)``
        where ``next_step`` is ``STEP_DONE`` on success or ``STEP_ERROR``
        on failure, ``error_msg`` is empty on success, and ``cal_factor``
        is 0.0 on failure.
    """
    known_weight = entry.weight_lbs
    if known_weight <= 0.0:
        return STEP_ERROR, "Weight must be greater than zero.", 0.0

    if not load_readings:
        return STEP_ERROR, "No readings received. Check wiring.", 0.0

    loaded_median = statistics.median(load_readings)
    net_raw = loaded_median - zero_offset
    if net_raw == 0:
        return STEP_ERROR, "No weight detected. Check wiring.", 0.0

    cal_factor = net_raw / known_weight
    try:
        write_calibration_json(zero_offset, cal_factor)
    except OSError as exc:
        return STEP_ERROR, f"Could not write config: {exc}", 0.0

    return STEP_DONE, "", cal_factor


def run_calibration(reader: WeightReader, maint_btn) -> None:  # pylint: disable=too-many-statements,too-many-branches,too-many-locals
    """Runs the full pygame calibration state machine.

    Drives the display through all calibration steps and writes the
    result to calibration.json on success.  Exits via ``sys.exit()``
    on completion or unrecoverable error so systemd restarts the main
    scale service.

    Args:
        reader: An initialised WeightReader backend (HX711 or NAU7802).
        maint_btn: A gpiozero Button on GPIO 18, or None.

    Raises:
        SystemExit: Always — 0 on success, 1 on error.
    """
    pygame.display.init()
    pygame.font.init()

    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)
    width, height = screen.get_size()

    fonts = {
        "title": load_font(height * 0.07),
        "sub":   load_font(height * 0.05),
        "cta":   load_font(height * 0.10),
        "debug": load_font(height * 0.03),
    }

    step = STEP_CLEAR
    _log.info("Step: %s", step)
    zero_readings: list = []
    load_readings: list = []
    zero_offset = 0
    entry = DigitEntry()
    step_start = time.time()
    cal_factor = 0.0
    error_msg = ""

    # Button state
    btn_down_time = 0.0
    btn_was_pressed = False

    clock = pygame.time.Clock()

    while True:
        clock.tick(TARGET_FPS)
        now = time.time()

        # ---- Event handling ----
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit(0)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit()
                sys.exit(0)

        # ---- Button edge detection ----
        is_pressed = bool(maint_btn and maint_btn.is_pressed)
        just_released = btn_was_pressed and not is_pressed

        if is_pressed and btn_down_time == 0.0:
            btn_down_time = now
        if not is_pressed:
            btn_down_time = 0.0
        btn_was_pressed = is_pressed

        press_duration = (now - btn_down_time) if btn_down_time > 0.0 else 0.0
        is_long = just_released and press_duration >= LONG_PRESS_SEC
        is_short = just_released and press_duration < LONG_PRESS_SEC

        # ---- State machine ----
        if step == STEP_CLEAR:
            _draw_prompt(screen, fonts, width, height,
                         "REMOVE ALL WEIGHT", "PRESS BUTTON WHEN READY",
                         step_index=1)
            if is_short or is_long:
                zero_readings = []
                step = STEP_ZERO
                step_start = now
                _log.info("Step: %s", step)

        elif step == STEP_ZERO:
            raw = reader.read_raw()
            if raw is not None:
                zero_readings.append(raw)
            progress = len(zero_readings) / SAMPLE_COUNT
            _draw_sampling(screen, fonts, width, height,
                           "CAPTURING ZERO READING...", progress,
                           step_index=2)
            if len(zero_readings) >= SAMPLE_COUNT:
                zero_offset = int(statistics.median(zero_readings))
                _log.debug("Zero readings: mean=%.0f stdev=%.1f n=%d",
                           statistics.mean(zero_readings),
                           statistics.stdev(zero_readings),
                           len(zero_readings))
                load_readings = []
                step = STEP_PLACE
                step_start = now
                _log.info("Step: %s (zero_offset=%d)", step, zero_offset)

        elif step == STEP_PLACE:
            _draw_prompt(screen, fonts, width, height,
                         "PLACE KNOWN WEIGHT ON SCALE",
                         "PRESS BUTTON WHEN READY",
                         step_index=3)
            if is_short or is_long:
                load_readings = []
                step = STEP_LOAD
                step_start = now
                _log.info("Step: %s", step)

        elif step == STEP_LOAD:
            raw = reader.read_raw()
            if raw is not None:
                load_readings.append(raw)
            progress = len(load_readings) / SAMPLE_COUNT
            _draw_sampling(screen, fonts, width, height,
                           "CAPTURING LOADED READING...", progress,
                           step_index=4)
            if len(load_readings) >= SAMPLE_COUNT:
                if not load_readings or not zero_readings:
                    error_msg = "No readings received. Check wiring."
                    _log.error("Step: %s — %s", STEP_ERROR, error_msg)
                    step = STEP_ERROR
                    step_start = now
                else:
                    _log.debug("Load readings: mean=%.0f stdev=%.1f n=%d",
                               statistics.mean(load_readings),
                               statistics.stdev(load_readings),
                               len(load_readings))
                    entry = DigitEntry()
                    step = STEP_ENTER
                    step_start = now
                    _log.info("Step: %s", step)

        elif step == STEP_ENTER:
            _draw_digit_entry(screen, fonts, width, height, entry)
            if not entry.confirmed:
                if is_short:
                    entry.digits[entry.active] = (
                        entry.digits[entry.active] + 1) % 10
                elif is_long:
                    if entry.active < entry.NUM_DIGITS - 1:
                        entry.active += 1
                    else:
                        entry.confirmed = True
            if entry.confirmed:
                step, error_msg, cal_factor = _confirm_entry(
                    entry, zero_offset, load_readings)
                step_start = now
                if step == STEP_DONE:
                    _log.info("Step: %s (factor=%.4f)", step, cal_factor)
                else:
                    _log.error("Step: %s — %s", step, error_msg)

        elif step == STEP_DONE:
            elapsed = now - step_start
            remaining = max(0, int(RESTART_DELAY - elapsed) + 1)
            _draw_done(screen, fonts, width, height, cal_factor, remaining)
            if elapsed >= RESTART_DELAY:
                pygame.quit()
                reader.close()
                sys.exit(0)

        elif step == STEP_ERROR:
            elapsed = now - step_start
            _draw_error(screen, fonts, width, height, error_msg)
            if elapsed >= ERROR_DISPLAY_SEC:
                pygame.quit()
                reader.close()
                sys.exit(1)

        pygame.display.flip()


# ----------------------------
# Screen drawing helpers
# ----------------------------
def _draw_step_indicator(screen, fonts, height, step_index):
    """Draws a small step indicator at the bottom of the screen.

    Args:
        screen: The pygame display surface.
        fonts: Dictionary of named font objects.
        height: Screen height in pixels.
        step_index: 1-based step number to display.
    """
    draw_centered(screen, fonts["debug"],
                  f"STEP {step_index} OF 5",
                  height * 0.92, COLOR_MUTED)


def _draw_prompt(screen, fonts, width, height,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                 main_text, sub_text, step_index):
    """Draws a two-line prompt screen with step indicator.

    Args:
        screen: The pygame display surface.
        fonts: Dictionary of named font objects.
        width: Screen width in pixels.
        height: Screen height in pixels.
        main_text: Primary instruction text.
        sub_text: Secondary instruction text.
        step_index: 1-based step number.
    """
    screen.fill(COLOR_BG)
    draw_centered(screen, fonts["title"],
                  "CELESTIAL SCALE CALIBRATION", height * 0.15, COLOR_NASA_RED)
    draw_centered(screen, fonts["sub"], main_text, height * 0.42, COLOR_FG)
    pulse = 0.5 + 0.5 * math.sin(time.time() * 3)
    pulse_color = tuple(int(c * (0.6 + 0.4 * pulse)) for c in COLOR_ACCENT)
    draw_centered(screen, fonts["sub"], sub_text, height * 0.55, pulse_color)
    _draw_step_indicator(screen, fonts, height, step_index)
    _ = width  # unused but kept for consistent signature


def _draw_sampling(screen, fonts, width, height,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                   caption, progress, step_index):
    """Draws a sampling screen with progress bar.

    Args:
        screen: The pygame display surface.
        fonts: Dictionary of named font objects.
        width: Screen width in pixels.
        height: Screen height in pixels.
        caption: Instruction text shown above the progress bar.
        progress: Fill fraction from 0.0 to 1.0.
        step_index: 1-based step number.
    """
    screen.fill(COLOR_BG)
    draw_centered(screen, fonts["title"],
                  "CELESTIAL SCALE CALIBRATION", height * 0.15, COLOR_NASA_RED)
    draw_centered(screen, fonts["sub"], caption, height * 0.42, COLOR_ACCENT)
    draw_progress_bar(screen, progress, width, height)
    _draw_step_indicator(screen, fonts, height, step_index)


def _draw_digit_entry(screen, fonts, width, height, entry):
    """Draws the numeric weight entry screen.

    Args:
        screen: The pygame display surface.
        fonts: Dictionary of named font objects.
        width: Screen width in pixels.
        height: Screen height in pixels.
        entry: The current DigitEntry state.
    """
    screen.fill(COLOR_BG)
    draw_centered(screen, fonts["title"],
                  "CELESTIAL SCALE CALIBRATION", height * 0.15, COLOR_NASA_RED)
    draw_centered(screen, fonts["sub"],
                  "ENTER KNOWN WEIGHT (LBS):", height * 0.35, COLOR_FG)
    entry.draw(screen, fonts, width, height)
    draw_centered(screen, fonts["debug"],
                  "SHORT PRESS: +1   LONG PRESS: NEXT / CONFIRM",
                  height * 0.72, COLOR_MUTED)
    _draw_step_indicator(screen, fonts, height, 5)


def _draw_done(screen, fonts, width, height, cal_factor,  # pylint: disable=too-many-arguments,too-many-positional-arguments
               seconds_left):
    """Draws the success screen with calibration factor and countdown.

    Args:
        screen: The pygame display surface.
        fonts: Dictionary of named font objects.
        width: Screen width in pixels.
        height: Screen height in pixels.
        cal_factor: Computed calibration factor (counts per lb).
        seconds_left: Seconds remaining before auto-exit.
    """
    screen.fill(COLOR_BG)
    draw_centered(screen, fonts["title"],
                  "CALIBRATION COMPLETE!", height * 0.35, COLOR_ACCENT)
    draw_centered(screen, fonts["sub"],
                  f"FACTOR: {cal_factor:.4f}", height * 0.50, COLOR_FG)
    draw_centered(screen, fonts["debug"],
                  f"RESTARTING IN {seconds_left}s...", height * 0.65, COLOR_MUTED)
    _ = width  # unused but kept for consistent signature


def _draw_error(screen, fonts, width, height, message):
    """Draws the error screen.

    Args:
        screen: The pygame display surface.
        fonts: Dictionary of named font objects.
        width: Screen width in pixels.
        height: Screen height in pixels.
        message: Human-readable error description.
    """
    screen.fill(COLOR_BG)
    draw_centered(screen, fonts["title"],
                  "CALIBRATION ERROR", height * 0.35, COLOR_NASA_RED)
    draw_centered(screen, fonts["sub"], message, height * 0.50, COLOR_FG)
    draw_centered(screen, fonts["debug"],
                  "CHECK WIRING AND RESTART", height * 0.65, COLOR_MUTED)
    _ = width  # unused but kept for consistent signature


# ----------------------------
# Entry point
# ----------------------------
def _build_reader(adc_flag: str) -> Optional[WeightReader]:
    """Constructs the appropriate WeightReader for the given ADC flag.

    Args:
        adc_flag: ``"hx711"`` or ``"nau7802"``.

    Returns:
        An initialised WeightReader, or None on failure.
    """
    if adc_flag == "hx711":
        return HX711WeightReader()
    return NAU7802WeightReader()


def main() -> None:
    """Entry point: parses args, creates reader, runs calibration loop.

    Raises:
        SystemExit: Always raised by run_calibration on completion.
    """
    parser = argparse.ArgumentParser(description="Celestial Scale Calibration")
    parser.add_argument("--adc", choices=["hx711", "nau7802"],
                        default="hx711",
                        help="ADC backend: hx711 (pigpio) or nau7802 (I2C)")
    parser.add_argument("--log-level",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        default="INFO",
                        help="Logging verbosity (default: INFO)")
    args = parser.parse_args()
    setup_logging(args.log_level)
    _log.info("Calibration starting with ADC backend: %s", args.adc)

    reader = _build_reader(args.adc)

    maint_btn = None
    if Button:
        maint_btn = Button(BUTTON_PIN_BCM, pull_up=True)

    run_calibration(reader, maint_btn)


if __name__ == "__main__":
    main()
