#!/bin/bash
# ==============================================================================
# Script Name: bootstrap.sh
# Version: 2.2 (Post-Mortem Compliant + User PWD)
# ==============================================================================

set -e

# --- Configuration ---
REPO_RAW="https://raw.githubusercontent.com/usenix17/Celestial_Scale/main"
USER_NAME="oas"
USER_PASS="oas"  # Setting password to username as default
INSTALL_DIR="/home/$USER_NAME/celestial_scale"

# WiFi Credentials
WIFI_SSID="Your_SSID"
WIFI_PASS="Your_Password"
COUNTRY_CODE="US"

# 0. Root Check
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)"
   exit 1
fi

echo "Starting Celestial Scale Bootstrap..."

# 1. User Provisioning
if ! id "$USER_NAME" &>/dev/null; then
    echo "Creating service user: $USER_NAME..."
    useradd -m -s /bin/bash "$USER_NAME"
    
    # Set the password to the username
    echo "$USER_NAME:$USER_PASS" | chpasswd
    echo "Password for $USER_NAME set."

    # Add to groups for hardware access
    usermod -aG gpio,video,input,i2c,audio "$USER_NAME"
else
    echo "User $USER_NAME already exists. Updating password..."
    echo "$USER_NAME:$USER_PASS" | chpasswd
fi

# 2. System Dependencies & pigpio Fix
echo "Installing system packages..."
apt-get update
apt-get install -y build-essential python3-setuptools unzip wget curl git python3-pip python3-pygame python3-gpiozero wpasupplicant

# Build pigpio from source (since Trixie repo is missing it)
if ! apt-get install -y pigpio python3-pigpio; then
    echo "pigpio package missing. Building from source..."
    wget https://github.com/joan2937/pigpio/archive/master.zip -O /tmp/pigpio.zip
    unzip -q -o /tmp/pigpio.zip -d /tmp
    cd /tmp/pigpio-master
    make
    PATH=$PATH:/sbin:/usr/sbin make install
    cd /
fi

# 3. Directory Setup 
mkdir -p "$INSTALL_DIR/assets/fonts"

# 4. Download Source Files
echo "Fetching source files from GitHub..."
curl -L "$REPO_RAW/celestial_scale.py" -o "$INSTALL_DIR/celestial_scale.py"
curl -L "$REPO_RAW/calibrate.py" -o "$INSTALL_DIR/calibrate.py"
curl -L "$REPO_RAW/celestial.service" -o "/etc/systemd/system/celestial_scale.service"
curl -L "$REPO_RAW/assets/fonts/Nasalization%20Rg.otf" -o "$INSTALL_DIR/assets/fonts/Nasalization Rg.otf"

chmod +x "$INSTALL_DIR/celestial_scale.py"
chmod +x "$INSTALL_DIR/calibrate.py"

# 5. Permissions
echo "Setting file ownership for $USER_NAME..."
chown -R "$USER_NAME:$USER_NAME" "/home/$USER_NAME"

# Sudoers for clean shutdown/reboot from Python
echo "${USER_NAME} ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot" > /etc/sudoers.d/celestial-poweroff
chmod 440 /etc/sudoers.d/celestial-poweroff

# 6. Networking Optimization
echo "Disabling NetworkManager and enabling systemd-networkd..."
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

# 7. Enable Services
echo "Enabling services..."
systemctl daemon-reload
systemctl enable pigpiod
systemctl enable systemd-networkd
systemctl enable systemd-resolved
systemctl enable wpa_supplicant@wlan0.service
systemctl enable celestial_scale.service

echo -e "\n========================================="
echo "  BOOTSTRAP COMPLETE"
echo "  User: $USER_NAME / Pass: $USER_PASS"
echo "  System optimized for embedded kiosk use."
echo "========================================="
