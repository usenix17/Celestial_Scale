#!/bin/bash
# ==============================================================================
# Script Name: bootstrap.sh
# Description: Automated install for Celestial Scale. 
# Rebuild Version: Feb 2026 - Post-Mortem compliant.
# ==============================================================================

set -e

# --- Configuration ---
REPO_RAW="https://raw.githubusercontent.com/usenix17/Celestial_Scale/main"
INSTALL_DIR="/home/oas/celestial_scale"
USER_NAME="oas"

# WiFi Credentials
WIFI_SSID="Your_SSID"
WIFI_PASS="Your_Password"
COUNTRY_CODE="US"

echo "Starting Celestial Scale Bootstrap..."

# 1. System Dependencies & pigpio Fix
echo "Installing system packages and hardware libraries..."
apt-get update
apt-get install -y build-essential python3-setuptools unzip wget curl git python3-pip python3-pygame python3-gpiozero wpasupplicant

# Attempt pigpio install; build from source if apt fails
if ! apt-get install -y pigpio python3-pigpio; then
    echo "pigpio package not found. Building from source for Trixie compatibility..."
    wget https://github.com/joan2937/pigpio/archive/master.zip -O /tmp/pigpio.zip
    unzip -q -o /tmp/pigpio.zip -d /tmp
    cd /tmp/pigpio-master
    make
    # Manually adding /sbin to PATH to ensure ldconfig is found
    PATH=$PATH:/sbin:/usr/sbin make install
    cd /
fi

# 2. Directory Setup
mkdir -p "$INSTALL_DIR/assets/fonts"

# 3. Download Source Files
echo "Fetching source files from GitHub..."
curl -L "$REPO_RAW/celestial_scale.py" -o "$INSTALL_DIR/celestial_scale.py"
curl -L "$REPO_RAW/calibrate.py" -o "$INSTALL_DIR/calibrate.py"
curl -L "$REPO_RAW/celestial.service" -o "/etc/systemd/system/celestial_scale.service"
curl -L "$REPO_RAW/assets/fonts/Nasalization%20Rg.otf" -o "$INSTALL_DIR/assets/fonts/Nasalization Rg.otf"

chmod +x "$INSTALL_DIR/celestial_scale.py"
chmod +x "$INSTALL_DIR/calibrate.py"

# 4. Permissions & Sudoers
chown -R $USER_NAME:$USER_NAME "$INSTALL_DIR"
echo "${USER_NAME} ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff" > /etc/sudoers.d/celestial-poweroff
chmod 440 /etc/sudoers.d/celestial-poweroff

# 5. Networking: Eliminate NetworkManager
echo "Optimizing network stack (Switching to systemd-networkd)..."
systemctl stop NetworkManager || true
systemctl disable NetworkManager || true
systemctl mask NetworkManager

cat <<EOF > /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=${COUNTRY_CODE}

network={
    ssid="${WIFI_SSID}"
    psk="${WIFI_PASS}"
}
EOF
chmod 600 /etc/wpa_supplicant/wpa_supplicant-wlan0.conf

cat <<EOF > /etc/systemd/network/25-wireless.network
[Match]
Name=wlan0

[Network]
DHCP=yes
EOF

# 6. Enable Services
echo "Enabling optimized services..."
systemctl daemon-reload
systemctl enable pigpiod
systemctl enable celestial_scale.service
systemctl enable systemd-networkd
systemctl enable systemd-resolved
systemctl enable wpa_supplicant@wlan0.service

# 7. Hardware Optimization
echo "Applying Pi Zero performance and safety tweaks..."
CONFIG_PATH="/boot/firmware/config.txt"
[ ! -f "$CONFIG_PATH" ] && CONFIG_PATH="/boot/config.txt"

{
    echo "# Celestial Scale Optimizations"
    echo "disable_splash=1"
    echo "hdmi_force_hotplug=1"
    echo "dtparam=audio=on"
    echo "dtoverlay=disable-bt" 
} >> "$CONFIG_PATH"

echo -e "\n========================================="
echo "  BOOTSTRAP COMPLETE"
echo "  System: Debian Trixie / Pi Zero"
echo "  Primary Fixes: pigpio source build + systemd-networkd"
echo "========================================="
