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
WIFI_SSID="Your_SSID"
WIFI_PASS="Your_Password"
COUNTRY_CODE="US"

if [[ $EUID -ne 0 ]]; then echo "Run as root."; exit 1; fi

echo "Starting Celestial Scale Bootstrap..."

# 1. User Provisioning
if ! id "$USER_NAME" &>/dev/null; then
    useradd -m -s /bin/bash "$USER_NAME"
    echo "$USER_NAME:$USER_PASS" | chpasswd
    usermod -aG gpio,video,input,i2c,audio,render "$USER_NAME"
fi

# 2. System Dependencies (Added systemd-resolved)
echo "Installing system packages..."
apt-get update
apt-get install -y build-essential python3-setuptools unzip wget curl git \
    python3-pip python3-pygame python3-gpiozero wpasupplicant systemd-resolved

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

# 6. Disable cloud-init
touch /etc/cloud/cloud-init.disabled

# 7. Networking (Forced Masking)
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
Name=wlan0
[Network]
DHCP=yes
EOF

# 8. Service Activation
echo "Enabling services..."
systemctl enable pigpiod
systemctl enable systemd-networkd
systemctl enable systemd-resolved
systemctl enable wpa_supplicant@wlan0.service
systemctl enable celestial_scale.service

echo "Bootstrap complete. Exit chroot and boot the hardware."
echo ":)"
