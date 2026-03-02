#!/usr/bin/env python3
"""ADC backend drivers for the Celestial Scale weight reader.

Provides a ``WeightReader`` abstract base class and two concrete
implementations:

* ``HX711WeightReader`` — pigpio bit-bang GPIO (existing hardware)
* ``NAU7802WeightReader`` — smbus2 I2C (drop-in replacement ADC)

Both return raw 24-bit signed integers from a load-cell Wheatstone
bridge.  The signal-processing pipeline in ``celestial_scale.py``
operates on these raw values and is backend-agnostic.

Requires:
    HX711:   pigpio + pigpiod running (``systemctl start pigpiod``)
    NAU7802: smbus2 (``pip install smbus2``) and i2c-dev kernel module
"""

import abc
import time
from typing import Optional

try:
    import pigpio
except ImportError:
    pigpio = None  # type: ignore[assignment]

try:
    import smbus2
except ImportError:
    smbus2 = None  # type: ignore[assignment]


# ----------------------------
# Timing Utilities
# ----------------------------
def _busy_wait_us(microseconds):
    """Busy-waits for the specified number of microseconds.

    Uses a spin loop instead of ``time.sleep()`` for sub-millisecond
    accuracy. ``time.sleep()`` on Linux has ~1 ms minimum granularity
    due to kernel scheduling, which is too coarse for the HX711's
    timing requirements.

    Args:
        microseconds: Number of microseconds to wait.
    """
    end = time.perf_counter() + (microseconds / 1_000_000)
    while time.perf_counter() < end:
        pass


# ----------------------------
# Abstract Base Class
# ----------------------------
class WeightReader(abc.ABC):
    """Abstract interface for a 24-bit load-cell ADC backend.

    Concrete implementations must connect to hardware in ``__init__``
    and release it in ``close()``.  The ``read_raw()`` method performs
    a single conversion and returns the result, or ``None`` when no
    data is available (conversion not ready, timeout, or demo mode).
    """

    @abc.abstractmethod
    def read_raw(self) -> Optional[int]:
        """Returns one raw 24-bit signed ADC reading, or None.

        Returns:
            A signed integer in the range −8 388 608 .. +8 388 607,
            or None if the conversion is not ready or timed out.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Releases hardware resources (GPIO, I2C bus, etc.)."""


# ----------------------------
# HX711 Low-Level Driver
# ----------------------------
class HX711:
    """Low-level driver for the HX711 24-bit ADC using pigpio.

    The HX711 uses a proprietary serial protocol where data is clocked
    out one bit at a time.  pigpio's DMA-backed GPIO access is used to
    maintain consistent timing and avoid corruption from kernel
    preemption.

    Clock pulses use ``_busy_wait_us`` spin loops rather than
    ``time.sleep()`` for accurate microsecond timing on the Pi Zero's
    single-core ARM.

    Attributes:
        pi: A ``pigpio.pi()`` instance for GPIO access.
        dout_pin: BCM pin number for HX711 data output.
        sck_pin: BCM pin number for HX711 serial clock.
    """

    GAIN_PULSES = {128: 1, 64: 3, 32: 2}
    """dict: Maps gain values to the number of extra clock pulses required."""

    CLOCK_PULSE_US = 10
    """int: Clock pulse width in microseconds."""

    READY_TIMEOUT = 2.0
    """float: Maximum time to wait for HX711 ready signal in seconds."""

    SPIKE_THRESHOLD = 8_000_000
    """int: Raw ADC values beyond this are rejected as noise spikes."""

    def __init__(self, pi, dout_pin, sck_pin, gain=128):
        """Initializes the HX711 driver and configures GPIO pins.

        Args:
            pi: A connected ``pigpio.pi()`` instance.
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

        Waits for DOUT to go low, clocks out 24 bits with busy-wait
        timing, then sends extra pulses to set the gain for the next
        conversion.

        Returns:
            A signed integer representing the raw ADC value, or None if
            the reading is a garbage spike.

        Raises:
            TimeoutError: If the HX711 does not become ready within
                ``READY_TIMEOUT`` seconds.
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
# HX711 WeightReader Backend
# ----------------------------
class HX711WeightReader(WeightReader):
    """WeightReader implementation using the HX711 via pigpio bit-bang.

    Wraps the low-level ``HX711`` driver.  Connects to pigpiod at
    construction time.  Falls back to demo mode (all reads return None)
    if pigpio is unavailable or pigpiod is not running.

    Attributes:
        data_pin: BCM GPIO pin for HX711 DOUT.
        clock_pin: BCM GPIO pin for HX711 SCK.
    """

    def __init__(self, data_pin=5, clock_pin=6):
        """Initialises pigpio connection and HX711 driver.

        Args:
            data_pin: BCM GPIO pin for HX711 data output. Defaults to 5.
            clock_pin: BCM GPIO pin for HX711 serial clock. Defaults to 6.
        """
        self.data_pin = data_pin
        self.clock_pin = clock_pin
        self._pi = None
        self._hx = None

        if not pigpio:
            print("pigpio not available — HX711WeightReader running in demo mode")
            return

        self._pi = pigpio.pi()
        if not self._pi.connected:
            print("Could not connect to pigpiod — HX711WeightReader in demo mode")
            self._pi = None
            return

        self._hx = HX711(self._pi, self.data_pin, self.clock_pin, gain=128)

    def read_raw(self) -> Optional[int]:
        """Returns one raw 24-bit signed reading, or None.

        Returns None immediately in demo mode, and on spike rejection.
        Propagates ``TimeoutError`` if the HX711 stops responding so
        the caller can mark the sensor as disconnected.

        Returns:
            Signed int from the HX711, or None if demo mode or spike.

        Raises:
            TimeoutError: If DOUT does not go low within 2 seconds.
        """
        if self._hx is None:
            return None
        return self._hx.read_raw()

    def close(self) -> None:
        """Disconnects from pigpiod and releases the GPIO handle.

        Safe to call even if the connection was never established
        (demo mode).
        """
        if self._pi is not None:
            self._pi.stop()
            self._pi = None
            self._hx = None


# ----------------------------
# NAU7802 WeightReader Backend
# ----------------------------
class NAU7802WeightReader(WeightReader):
    """WeightReader implementation using the NAU7802 via smbus2 I2C.

    The NAU7802 is a 24-bit differential ADC with a hardware I2C
    interface (bus 1, address 0x2A on Raspberry Pi).  It replaces the
    HX711 bit-bang approach with a hardware-timed I2C peripheral,
    eliminating GPIO timing sensitivity.

    Falls back to demo mode (all reads return None) if smbus2 is
    unavailable or the I2C bus cannot be opened.

    Register references:
        NAU7802 Datasheet, Rev. 1.7 — Nuvoton Technology Corporation
    """

    _I2C_BUS = 1
    """int: Linux I2C bus number (/dev/i2c-1 on Raspberry Pi)."""

    _I2C_ADDR = 0x2A
    """int: NAU7802 fixed I2C device address."""

    # ---- Register map (§8. Register Description) ----
    _REG_PU_CTRL = 0x00
    """int: Power-Up Control register."""

    _REG_CTRL1 = 0x01
    """int: Control Register 1 (gain, LDO voltage)."""

    _REG_CTRL2 = 0x02
    """int: Control Register 2 (conversion rate, calibration)."""

    _REG_ADC_MSB = 0x12
    """int: ADC output MSB register (followed by 0x13, 0x14)."""

    # ---- PU_CTRL bit masks ----
    _PU_RR = 0x01    # Register Reset
    _PU_PUD = 0x02   # Power Up Digital
    _PU_PUA = 0x04   # Power Up Analog
    _PU_PUR = 0x08   # Power Up Ready (read-only)
    _PU_CS = 0x10    # Cycle Start
    _PU_CR = 0x20    # Cycle Ready (read-only)

    # ---- CTRL2 bit masks ----
    _CTRL2_CALS = 0x40   # Calibration Start (self-clearing)
    _CTRL2_CAL_ERR = 0x08  # Calibration Error flag

    _INIT_TIMEOUT = 1.0
    """float: Max seconds to wait for power-up ready (PUR) bit."""

    _CAL_TIMEOUT = 1.0
    """float: Max seconds to wait for AFE offset calibration."""

    _READ_TIMEOUT = 0.2
    """float: Max seconds to poll for Cycle Ready (CR) bit per read."""

    def __init__(self):
        """Opens the I2C bus and runs the full NAU7802 init sequence.

        Init sequence (NAU7802 datasheet §8.1):
            1. Reset all registers (RR bit in PU_CTRL).
            2. Power up digital + analog blocks (PUD + PUA).
            3. Wait for PUR (power-up ready) bit.
            4. Set gain to 128x (CTRL1 GAINS[2:0] = 111).
            5. Set sample rate to 80 SPS (CTRL2 CRS[2:0] = 011).
            6. Run internal offset calibration (CTRL2 CALS + CALMOD=00).
            7. Start conversion cycles (PU_CTRL CS bit).
        """
        self._bus = None

        if not smbus2:
            print("smbus2 not available — NAU7802WeightReader in demo mode")
            return

        try:
            self._bus = smbus2.SMBus(self._I2C_BUS)
            self._init_chip()
        except OSError as exc:
            print(f"NAU7802 I2C open failed ({exc}) — running in demo mode")
            self._bus = None

    def _write(self, reg, value):
        """Writes a single byte to a NAU7802 register.

        Args:
            reg: Register address (8-bit).
            value: Byte value to write (8-bit).
        """
        self._bus.write_byte_data(self._I2C_ADDR, reg, value)

    def _read(self, reg):
        """Reads a single byte from a NAU7802 register.

        Args:
            reg: Register address (8-bit).

        Returns:
            The register value as an unsigned 8-bit integer.
        """
        return self._bus.read_byte_data(self._I2C_ADDR, reg)

    def _init_chip(self):
        """Runs the full NAU7802 power-up and calibration sequence.

        Raises:
            TimeoutError: If PUR or CALS does not clear within timeout.
            OSError: On I2C communication failure.
            RuntimeError: If the AFE offset calibration reports an error.
        """
        # Step 1: Reset all registers (RR=1), then release reset
        # PU_CTRL § 8.1 — RR clears all registers to their reset values
        self._write(self._REG_PU_CTRL, self._PU_RR)
        time.sleep(0.001)

        # Step 2: Power up digital (PUD) and analog (PUA) sections
        self._write(self._REG_PU_CTRL, self._PU_PUD | self._PU_PUA)

        # Step 3: Wait for PUR (Power Up Ready) — typically <200 ms
        deadline = time.time() + self._INIT_TIMEOUT
        while not self._read(self._REG_PU_CTRL) & self._PU_PUR:
            if time.time() > deadline:
                raise TimeoutError("NAU7802 power-up ready (PUR) timeout")
            time.sleep(0.01)

        # Step 4: Set gain to 128× — CTRL1 GAINS[2:0] = 0b111
        # CTRL1 § 8.3: bits [2:0] select gain: 111 = 128×
        self._write(self._REG_CTRL1, 0x07)

        # Step 5: Set conversion rate to 80 SPS — CTRL2 CRS[2:0] = 0b011
        # CTRL2 § 8.4: bits [2:0] select rate: 011 = 80 SPS
        self._write(self._REG_CTRL2, 0x03)

        # Step 6: Start internal offset calibration
        # CTRL2 § 8.4: CALMOD[5:4]=00 (internal offset), CALS[6]=1 (start)
        # CRS bits preserved at 011 (80 SPS)
        self._write(self._REG_CTRL2, 0x43)

        # Step 7: Wait for CALS to self-clear (calibration complete)
        deadline = time.time() + self._CAL_TIMEOUT
        while self._read(self._REG_CTRL2) & self._CTRL2_CALS:
            if time.time() > deadline:
                raise TimeoutError("NAU7802 AFE calibration timeout")
            time.sleep(0.01)

        # Check for calibration error flag
        if self._read(self._REG_CTRL2) & self._CTRL2_CAL_ERR:
            raise RuntimeError("NAU7802 AFE offset calibration reported error")

        # Step 8: Start conversion cycles — set CS bit alongside PUD + PUA
        # PU_CTRL § 8.1: CS=1 initiates continuous conversion cycles
        self._write(self._REG_PU_CTRL, self._PU_PUD | self._PU_PUA | self._PU_CS)

    def read_raw(self) -> Optional[int]:
        """Polls for a completed conversion and returns the ADC result.

        Polls the CR (Cycle Ready) bit in PU_CTRL with a short timeout.
        Returns None if no conversion is ready within the timeout —
        this is normal behaviour between conversions at 80 SPS (~12.5 ms
        per cycle) and does not indicate a hardware fault.

        Returns:
            Signed 24-bit integer (range −8 388 608 .. +8 388 607), or
            None if in demo mode or conversion not ready.

        Raises:
            OSError: On I2C communication failure.
        """
        if self._bus is None:
            return None

        # Poll CR bit (PU_CTRL[5]) for a fresh conversion result
        deadline = time.time() + self._READ_TIMEOUT
        while not self._read(self._REG_PU_CTRL) & self._PU_CR:
            if time.time() > deadline:
                return None   # Not ready yet — caller will retry next frame
            time.sleep(0.002)

        # Read 3 bytes: 0x12 (MSB), 0x13, 0x14 (LSB)
        data = self._bus.read_i2c_block_data(self._I2C_ADDR, self._REG_ADC_MSB, 3)
        raw = (data[0] << 16) | (data[1] << 8) | data[2]

        # Sign-extend from 24-bit two's complement
        if raw & 0x800000:
            raw -= 0x1000000

        return raw

    def close(self) -> None:
        """Powers down the NAU7802 and closes the I2C bus.

        Writes 0x00 to PU_CTRL, clearing PUD and PUA to enter power-down
        mode (PU_CTRL § 8.1).  Safe to call in demo mode.
        """
        if self._bus is not None:
            try:
                # Clear PUD + PUA + CS → full power down
                self._write(self._REG_PU_CTRL, 0x00)
            except OSError:
                pass
            self._bus.close()
            self._bus = None
