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

### 2. Configure WiFi Credentials
Before running the bootstrap, ensure your bootstrap.sh script is updated with your specific network credentials:

Open `bootstrap.sh` on your host machine.

Update the `WIFI_SSID` and `WIFI_PASS` variables:

```bash
WIFI_SSID="Your_Network_Name"
WIFI_PASS="Your_Password"
```

### 3. Copy bootstrap.sh into the chroot

From a second terminal on the host (while the chroot is still open), copy the bootstrap script into the mounted filesystem:

```bash
sudo cp bootstrap.sh /mnt/pi_root/root/bootstrap.sh
```

### 4. Run the bootstrap

Back inside the chroot, run the script:

```bash
bash /root/bootstrap.sh
```

This will:
- Configure WiFi via `systemd_networkd`
- Install system dependencies (`pigpio`, `python3-pygame`, etc.)
- Download `celestial_scale.py` and `celestial.service` from GitHub
- Enable the `pigpiod` and `celestial_scale` systemd services
- Apply boot config tweaks for kiosk use

### 5. Exit the chroot

```bash
exit
```

The cleanup trap in `pi-chroot.sh` will unmount everything automatically.

---

The SD card is now ready. Insert it into the Pi and power it on — the scale application will start automatically on boot.
