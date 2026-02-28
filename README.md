# INCIDENT POST-MORTEM
## Celestial Scale — Planetary Weight Display
*Astronomical Club Kiosk Project*

---

| Field | Detail |
|-------|--------|
| **Date** | February 2026 |
| **Severity** | Total System Failure — Non-operational |
| **Status** | Root causes identified; rebuild in progress |

---

## Executive Summary

The Celestial Scale, a public-facing kiosk that displays a person's weight on various celestial bodies, was presented as non-functional. Investigation revealed multiple independent hardware failures and significant software architecture issues. The system could not boot.

---

## Timeline of Investigation

| Phase | Finding |
|-------|---------|
| **Initial Triage** | System would not power on. USB-C power delivery measured at 21 mV — effectively zero. |
| **Power Fix** | USB-C connector lacked required 5.1kΩ CC pull-down resistors. Switching to a USB-A to USB-C cable bypassed CC negotiation and delivered 5V. Board still did not boot. |
| **SD Card** | SD card reported as 121 MB instead of 29 GB. Intermittent detection on host workstation. Card was dragging down the Pi's 3.3V rail, preventing the SoC from powering on entirely. |
| **HX711 Testing** | With inputs shorted (known zero reference), the HX711 ADC returned wildly inconsistent values ranging from -8 million to +8 million. A functioning HX711 should return near-identical values. Chip confirmed damaged. |
| **Load Cells** | Intermittent connections at the combiner board. One cell initially showed open-circuit on two wire pairs due to a loose connector.|

---

## Root Cause Analysis

### Hardware Failures

#### 1. SD Card Failure (Critical)

The SD card's flash controller had failed, causing it to misreport its capacity and intermittently drop off the bus. When inserted, the card's internal short dragged the Pi's 3.3V regulator output to near zero, preventing the SoC from starting. This single failure made the entire system appear dead despite functional compute hardware.

#### 2. HX711 ADC Failure (Critical)

The SparkFun HX711 load cell amplifier was producing corrupted readings. Even with its analog inputs shorted together (a known-zero test condition), the 24-bit ADC returned values spanning the full range instead of a stable baseline. The chip appears to be damaged, likely from an overvoltage or short circuit event.

#### 3. USB-C Power Delivery (Design Flaw)

The custom USB-C power input lacked the required 5.1kΩ pull-down resistors on the CC1 and CC2 pins. Per the USB-C specification, a source will not enable VBUS without detecting these resistors. This made the system appear unpowered when using any USB-C to USB-C cable.

### Software & Configuration Issues

#### 1. `cloud-init` on a Kiosk

The Raspberry Pi OS image was configured with `cloud-init`, a provisioning framework designed for cloud virtual machines. `cloud-init` was actively rewriting user configuration files (including `.bashrc`) on every boot, overriding manual changes. `cloud-init` can not be disabled through standard systemctl commands, as it re-enabled its own services through multiple persistence mechanisms including `init.d` scripts, `deb-systemd-helper` state files, and target dependencies. `cloud-init` added approximately 40 seconds to every boot cycle and served no purpose on a standalone kiosk.

#### 2. `NetworkManager`

`NetworkManager` was the single slowest service at boot, consuming 56 seconds. For a kiosk that only needs Wi-Fi for occasional SSH maintenance, `systemd-networkd` with `wpa_supplicant` achieves the same result in under 2 seconds. The NetworkManager-wait-online service additionally blocked the boot sequence until a full network connection was established, which is unnecessary for a device whose primary function does not require network.

#### 3. Application Autostart via `.bashrc`

The scale application was launched by appending a python3 exec line to the user's `.bashrc`, gated on `tty1`. While functional, this approach is fragile: it doesn't support automatic restart on crash, has no logging, doesn't handle dependencies (such as `pigpiod` needing to start first), and was being overwritten by `cloud-init` on every boot. A `systemd` service unit is the correct mechanism for this.

#### 4. Unnecessary RP2040 Middleman

The design included both a Raspberry Pi Zero and an RP2040-Zero microcontroller. The RP2040 read the HX711 load cell amplifier and forwarded weight data to the Pi over UART serial. While this architecture solves the Linux GPIO timing problem (the Pi's kernel can preempt bit-bang reads), it doubles the hardware, wiring, and failure surface. Using the pigpio library with its DMA-based hardware-timed GPIO on the Pi alone provides reliable HX711 reads without the second microcontroller.

#### 5. Feeding 5V Through GPIO Header

The original design powered the Pi through the GPIO header's 5V pins rather than the micro-USB port. This bypasses the Pi's onboard polyfuse, which provides overcurrent protection. Any wiring fault sends unprotected current directly to the SoC. This is likely the mechanism that damaged the original Pi Zero and possibly the HX711.

---

## Unnecessary Services at Boot

The following services were enabled on a headless kiosk with no need for printing, Bluetooth, modem, or color management:

| Service | Boot Time | Purpose |
|---------|-----------|---------|
| `NetworkManager` | 56.4s | Network management (replaceable with systemd-networkd) |
| `cloud-init` (4 stages) | 46.3s | Cloud VM provisioning (no purpose on a Pi) |
| `dpkg-db-backup` | 15.4s | Package database backup |
| `cups` / `cups-browsed` | 3.0s | Print server |
| `avahi-daemon` | 3.4s | mDNS/DNS-SD service discovery |
| `ModemManager` | 2.5s | Cellular modem management |
| `colord` | 3.4s | Color management daemon |
| `polkit` | 4.0s | Authorization framework |
| **Total waste** | **~134s** | **Over 2 minutes of unnecessary boot time** |

---

## Damaged Components

| Component | Status | Evidence |
|-----------|--------|----------|
| SD Card (PNY) | **FAULTED** | Reports 121 MB capacity; intermittent bus detection; drags 3.3V rail to 0V when inserted |
| SparkFun HX711 | **FAULTED** | Returns full-range random values with inputs shorted; 24-bit ADC non-functional |
| Raspberry Pi Zero (original) | **POSSIBLY OK** | Could not boot due to SD card failure; may function with a working card |
| Load Cells (x4) | **FUNCTIONAL** | Correct resistance measurements across all wire pairs after reseating |
| SparkFun Combiner | **FUNCTIONAL** | Passive PCB; no active components to damage |
| Tare Button | **FUNCTIONAL** | Simple momentary switch; confirmed working via GPIO test |
| HDMI Screen | **FUNCTIONAL** | Displayed output when Pi was able to boot |

---

## Recommendations for Rebuild

1. **Replace the SD card** with a name-brand card (Samsung, SanDisk) from a reputable seller. Counterfeit and failing SD cards are the most common Pi failure mode.
2. **Replace the HX711 board.** SparkFun or generic green breakouts are functionally identical (~$6–11).
3. **Eliminate the RP2040.** Use `pigpio` on the Pi for hardware-timed HX711 reads. Fewer components, fewer wires, fewer failure modes.
4. **Power via micro-USB, not GPIO header.** This preserves the onboard polyfuse protection.
5. **Use Raspberry Pi OS Lite.** No desktop environment, no cloud-init, no unnecessary services. Flash without Raspberry Pi Imager customizations to avoid cloud-init injection.
6. **Replace NetworkManager with systemd-networkd + wpa_supplicant.** Saves 56+ seconds at boot.
7. **Use a systemd service for the application.** Provides automatic restart, dependency ordering, and journald logging instead of a .bashrc hack.
---

## Recommended Signal Chain

```
4x Load Cells → Combiner Board → HX711 (E+/E–/A+/A–) → Pi GPIO 5 & 6 → HDMI Display
```

**GPIO Pin Assignments:**

| Function | GPIO | Physical Pin |
|----------|------|-------------|
| HX711 DOUT | GPIO 5 | Pin 29 |
| HX711 SCK | GPIO 6 | Pin 31 |
| Tare Button | GPIO 18 | Pin 12 |
| HX711 VCC | 5V | Pin 2 |
| HX711 GND | GND | Pin 6 |
| Button GND | GND | Pin 14 |

---

## Conclusion

The Celestial Scale's failure was caused by a combination of a physically failing SD card, a faulted HX711 ADC, and a software stack with unnecessary complexity. No single fix would have resolved the system. The SD card failure alone made the device appear completely dead.

The recommended rebuild eliminates the RP2040 middleman, uses pigpio for reliable ADC reads, powers the system safely through the micro-USB port, and runs a minimal OS image with a proper `systemd` service. Updated code and wiring diagrams have been prepared and are ready for deployment once replacement hardware is available.
