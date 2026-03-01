#!/bin/bash
# ==============================================================================
# Script Name: bootstrap.sh
# Version: 3.1 (Chroot-Safe / Resolved Fix)
# ==============================================================================

set -e

# --- PATH FIX ---
export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# --- Configuration ---
REPO_RAW="https://raw.githubusercontent.com/usenix17/Celestial_Scale/main"
USER_NAME="oas"
USER_PASS="oas"
INSTALL_DIR="/home/$USER_NAME/celestial_scale"
COUNTRY_CODE="US"

if [[ $EUID -ne 0 ]]; then echo "Run as root."; exit 1; fi

read -rp "WiFi SSID: " WIFI_SSID
read -rsp "WiFi Password: " WIFI_PASS
echo

# Ensure DNS works inside the chroot
echo "nameserver 8.8.8.8" > /etc/resolv.conf

echo "Starting Celestial Scale Bootstrap..."

# 1. User Provisioning
if ! id "$USER_NAME" &>/dev/null; then
    useradd -m -s /bin/bash "$USER_NAME"
    echo "$USER_NAME:$USER_PASS" | chpasswd
    usermod -aG gpio,video,input,i2c,audio,render,tty "$USER_NAME"
fi

# 2. System Dependencies (Added systemd-resolved)
echo "Installing system packages..."
apt-get update
apt-get install -y build-essential python3-setuptools unzip wget curl git \
    python3-pip python3-pygame python3-gpiozero wpasupplicant systemd-resolved \
    libegl1 libgles2 libgl1-mesa-dri libgbm1 kbd cage seatd vim

# 3. pigpio Build & Service Setup
if [ ! -f "/usr/local/bin/pigpiod" ]; then
    echo "Building pigpio from source..."
    wget https://github.com/joan2937/pigpio/archive/master.zip -O /tmp/pigpio.zip
    unzip -q -o /tmp/pigpio.zip -d /tmp
    cd /tmp/pigpio-master && make && make install && cd /
    
    cat <<EOF > /etc/systemd/system/pigpiod.service
[Unit]
Description=Daemon required for pigpio
[Service]
ExecStart=/usr/local/bin/pigpiod -l
ExecStop=/bin/systemctl kill pigpiod
Type=forking
[Install]
WantedBy=multi-user.target
EOF
fi

# 4. Directory & Source Fetch
mkdir -p "$INSTALL_DIR/assets/fonts"
curl -L "$REPO_RAW/celestial_scale.py" -o "$INSTALL_DIR/celestial_scale.py"
curl -L "$REPO_RAW/calibrate.py" -o "$INSTALL_DIR/calibrate.py"
curl -L "$REPO_RAW/celestial.service" -o "/etc/systemd/system/celestial_scale.service"
curl -L "$REPO_RAW/assets/fonts/Nasalization%20Rg.otf" -o "$INSTALL_DIR/assets/fonts/Nasalization Rg.otf"
chmod +x "$INSTALL_DIR/celestial_scale.py" "$INSTALL_DIR/calibrate.py"

# 5. Permissions
chown -R "$USER_NAME:$USER_NAME" "/home/$USER_NAME"
echo "${USER_NAME} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/oas-nopasswd
chmod 440 /etc/sudoers.d/oas-nopasswd

# Create environment file with runtime dir for the user's uid (used by systemd service)
echo "XDG_RUNTIME_DIR=/run/user/$(id -u $USER_NAME)" > /etc/celestial-env

# Enable linger so systemd creates /run/user/UID on boot without a login session.
# loginctl enable-linger doesn't work in a chroot (no D-Bus), so write the file directly.
mkdir -p /var/lib/systemd/linger
touch /var/lib/systemd/linger/"$USER_NAME"

# 6. Disable cloud-init
touch /etc/cloud/cloud-init.disabled

# 7. Disable unnecessary services
# Mask rather than disable — prevents them starting even as a dependency.
# ln -sf /dev/null is equivalent to systemctl mask and works safely in a chroot.
echo "Masking unnecessary services..."
for SVC in \
    ModemManager \
    bluetooth \
    cups \
    cups-browsed \
    avahi-daemon \
    colord \
    polkit \
    dpkg-db-backup \
    apt-daily \
    apt-daily-upgrade \
    man-db \
    e2scrub_reap \
    NetworkManager \
    NetworkManager-wait-online \
    NetworkManager-dispatcher \
    NetworkManager-sleep \
    rpi-eeprom-update \
    raspi-config \
    userconfig \
    getty@tty1; do
    ln -sf /dev/null "/etc/systemd/system/${SVC}.service"
done

# Mask unnecessary timers
for TIMER in \
    apt-daily \
    apt-daily-upgrade \
    man-db \
    e2scrub_reap \
    dpkg-db-backup \
    logrotate \
    fstrim; do
    ln -sf /dev/null "/etc/systemd/system/${TIMER}.timer"
done

# 8. Networking (Forced Masking)
echo "Configuring networkd..."
ln -sf /dev/null /etc/systemd/system/NetworkManager.service

cat <<EOF > /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=${COUNTRY_CODE}
network={
    ssid="${WIFI_SSID}"
    psk="${WIFI_PASS}"
}
EOF

cat <<EOF > /etc/systemd/network/25-wireless.network
[Match]
Name=wl*
[Network]
DHCP=yes
EOF

# 9. rfkill unblock WiFi at boot
cat <<EOF > /etc/systemd/system/rfkill-unblock-wifi.service
[Unit]
Description=Unblock WiFi via rfkill
Before=wpa_supplicant@wlan0.service
[Service]
Type=oneshot
ExecStart=/usr/sbin/rfkill unblock wifi
[Install]
WantedBy=multi-user.target
EOF

# 10. Service Activation
echo "Enabling services..."
# seatd may not create the seat group automatically on Debian
groupadd -r seat 2>/dev/null || true
usermod -aG seat "$USER_NAME"

systemctl enable pigpiod
systemctl enable seatd
systemctl enable ssh
systemctl enable rfkill-unblock-wifi
systemctl enable systemd-networkd
systemctl enable systemd-resolved
systemctl enable wpa_supplicant@wlan0.service
systemctl enable celestial_scale.service

echo "Bootstrap complete. Exit chroot and boot the hardware."
echo ":)"
