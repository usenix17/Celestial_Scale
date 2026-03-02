# Installation

Installation is done from a host Linux machine with the Pi's SD card inserted, using a QEMU-powered chroot to run commands directly on the Pi's filesystem without booting it.

## Requirements

- A host Linux machine (x86_64)
- The Pi's SD card inserted and visible as a block device (e.g. `/dev/sdb`)
- `sudo` access on the host

## Steps

### 1. Enter the Pi chroot

Run `pi-chroot.sh` from the host machine as root. It will mount the SD card and drop you into an ARM chroot environment as if you were running commands on the Pi itself.

```bash
sudo ./pi-chroot.sh
```

When prompted:
- **Target Device**: enter the block device for your SD card (e.g. `sdb`)
- **Architecture**: `arm64` for Pi 4/5/Zero 2W, `armhf` for original Pi Zero
- **Expand partition**: optional, recommended if the card is larger than the image

### 2. Copy bootstrap.sh into the chroot

From a second terminal on the host (while the chroot is still open), copy the bootstrap script into the mounted filesystem:

```bash
sudo cp bootstrap.sh /mnt/pi_root/root/bootstrap.sh
```

### 3. Run the bootstrap

Back inside the chroot, run the script:

```bash
bash /root/bootstrap.sh
```

When prompted:
- **WiFi SSID and password** — used to configure `wpa_supplicant` for first-boot networking
- **ADC backend** — choose `[1] HX711` (GPIO bit-bang via pigpio) or `[2] NAU7802` (I2C)

The script will then:
- Install system dependencies (`pigpio`, `python3-pygame`, `python3-smbus`, etc.)
- Download `celestial_scale.py`, `calibrate.py`, `adc.py`, and the font from GitHub
- Download and enable the appropriate systemd service (`celestial-scale-hx711` or `celestial-scale-nau7802`)
- Write a placeholder `/etc/celestial-scale/calibration.json`
- Configure WiFi via `systemd-networkd` + `wpa_supplicant`
- Apply boot config tweaks for kiosk use (X11, pigpiod, etc.)

### 4. Exit the chroot

```bash
exit
```

The cleanup trap in `pi-chroot.sh` will unmount everything automatically.

---

The SD card is now ready. Insert it into the Pi and power it on — the scale application will start automatically on boot.

## Calibration

The scale ships with a placeholder calibration. Before use, run the on-screen calibration procedure using the maintenance button. See [CALIBRATE.md](CALIBRATE.md) for full instructions.

## Viewing logs

```bash
# Live log stream
journalctl -u celestial-scale-hx711 -f

# ADC health metrics (structured fields logged every 60 s)
journalctl -u celestial-scale-hx711 READS_OK=

# Increase verbosity (edit the service or pass flag manually)
python3 /home/oas/celestial_scale/celestial_scale.py --adc hx711 --log-level DEBUG
```

## Service management

```bash
# HX711 backend
sudo systemctl status celestial-scale-hx711
sudo systemctl restart celestial-scale-hx711

# NAU7802 backend
sudo systemctl status celestial-scale-nau7802
sudo systemctl restart celestial-scale-nau7802
```

Only one backend service should be enabled at a time. The bootstrap script masks the unused one automatically.
