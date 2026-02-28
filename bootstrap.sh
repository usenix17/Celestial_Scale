#!/bin/bash
# ==============================================================================
# Script Name: bootstrap.sh
# Description: Automated install for Celestial Scale with WiFi & systemd-networkd.
# ==============================================================================

set -e

# --- Configuration ---
REPO_RAW="https://raw.githubusercontent.com/usenix17/Celestial_Scale/main"
INSTALL_DIR="/home/oas/celestial_scale"
USER_NAME="oas"

# WiFi Credentials (Replace these or use environment variables)
WIFI_SSID="Your_SSID"
WIFI_PASS="Your_Password"
COUNTRY_CODE="US"

echo "Starting Celestial Scale Bootstrap from GitHub..."

# 1. System Dependencies
echo "Installing system packages..."
apt-get update
apt-get install -y \
    python3-pip python3-pygame pigpio python3-pigpio \
    python3-gpiozero git curl wget wpasupplicant

# 2. Directory Setup
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/assets/fonts"

# 3. Download Source Files
echo "Fetching source files from GitHub..."
curl -L "$REPO_RAW/celestial_scale.py" -o "$INSTALL_DIR/celestial_scale.py"
curl -L "$REPO_RAW/calibrate.py" -o "$INSTALL_DIR/calibrate.py"
curl -L "$REPO_RAW/celestial.service" -o "/etc/systemd/system/celestial_scale.service"
curl -L "$REPO_RAW/assets/fonts/Nasalization%20Rg.otf" -o "$INSTALL_DIR/assets/fonts/Nasalization Rg.otf"

chmod +x "$INSTALL_DIR/celestial_scale.py"
chmod +x "$INSTALL_DIR/calibrate.py"

# 4. Permissions
chown -R $USER_NAME:$USER_NAME "$INSTALL_DIR"

echo "${USER_NAME} ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff" \
    > /etc/sudoers.d/celestial-poweroff
chmod 440 /etc/sudoers.d/celestial-poweroff

# 5. WiFi & Networking Configuration (systemd-networkd)
echo "Configuring Networking and WiFi..."

# Setup wpa_supplicant
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

# Setup systemd-networkd profile for wlan0
cat <<EOF > /etc/systemd/network/25-wireless.network
[Match]
Name=wlan0

[Network]
DHCP=yes
EOF

# 6. Enable Services
echo "Enabling services..."
systemctl enable pigpiod
systemctl enable celestial_scale.service

# Switch to systemd-networkd
systemctl enable systemd-networkd
systemctl enable systemd-resolved
systemctl enable wpa_supplicant@wlan0.service

# 7. Kiosk Hardware Optimization
echo "Applying Pi Zero performance tweaks..."
CONFIG_PATH="/boot/firmware/config.txt"
[ ! -f "$CONFIG_PATH" ] && CONFIG_PATH="/boot/config.txt"

{
    echo "disable_splash=1"
    echo "hdmi_force_hotplug=1"
    echo "dtparam=audio=on"
} >> "$CONFIG_PATH"

echo -e "\n========================================="
echo "  BOOTSTRAP COMPLETE"
echo "  WiFi Configured: ${WIFI_SSID}"
echo "  Networking: systemd-networkd"
echo "========================================="
