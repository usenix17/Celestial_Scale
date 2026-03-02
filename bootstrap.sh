#!/bin/bash
# ==============================================================================
# Script Name: bootstrap.sh
# Version: 4.0 (The "No-Wayland" Production Version)
# ==============================================================================

set -e
export PATH=$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

REPO_RAW="https://raw.githubusercontent.com/usenix17/Celestial_Scale/main"
USER_NAME="oas"
USER_PASS="oas"
INSTALL_DIR="/home/$USER_NAME/celestial_scale"
COUNTRY_CODE="US"

if [[ $EUID -ne 0 ]]; then echo "Run as root."; exit 1; fi

read -rp "WiFi SSID: " WIFI_SSID
read -rsp "WiFi Password: " WIFI_PASS
echo
echo "nameserver 8.8.8.8" > /etc/resolv.conf

# 1. User & Groups
groupadd -r seat 2>/dev/null || true
if ! id "$USER_NAME" &>/dev/null; then
    useradd -m -s /bin/bash "$USER_NAME"
    echo "$USER_NAME:$USER_PASS" | chpasswd
fi
usermod -aG gpio,video,input,i2c,audio,render,tty,seat "$USER_NAME"

# 2. Dependencies
apt-get update
apt-get install -y build-essential python3-setuptools unzip wget curl git \
    python3-pip python3-pygame python3-gpiozero wpasupplicant systemd-resolved \
    libgl1-mesa-dri libgbm1 kbd dbus vim rfkill

# 3. pigpio Build (Standard Forking Service)
if [ ! -f "/usr/local/bin/pigpiod" ]; then
    wget https://github.com/joan2937/pigpio/archive/master.zip -O /tmp/pigpio.zip
    unzip -q -o /tmp/pigpio.zip -d /tmp
    cd /tmp/pigpio-master && make && make install && cd /
    cat <<EOF > /etc/systemd/system/pigpiod.service
[Unit]
Description=Daemon required for pigpio
[Service]
ExecStart=/usr/local/bin/pigpiod -l
Type=forking
[Install]
WantedBy=multi-user.target
EOF
fi

# 4. Kiosk Service (kmsdrm — no Wayland compositor needed)
cat <<EOF > /etc/systemd/system/celestial_scale.service
[Unit]
Description=Celestial Scale Kiosk
After=pigpiod.service

[Service]
Type=simple
User=${USER_NAME}
Group=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=/etc/celestial-env
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=NO_AT_BRIDGE=1

TTYPath=/dev/tty1
StandardInput=tty-force
StandardOutput=journal
StandardError=journal

ExecStartPre=+/usr/bin/chvt 1

ExecStart=/usr/bin/python3 ${INSTALL_DIR}/celestial_scale.py

Restart=on-failure
RestartSec=5
SupplementaryGroups=video input render tty gpio

[Install]
WantedBy=multi-user.target
EOF

# 6. Assets & Permissions
mkdir -p "$INSTALL_DIR/assets/fonts"
curl -L "$REPO_RAW/celestial_scale.py" -o "$INSTALL_DIR/celestial_scale.py"
curl -L "$REPO_RAW/calibrate.py" -o "$INSTALL_DIR/calibrate.py"
curl -L "$REPO_RAW/assets/fonts/Nasalization%20Rg.otf" -o "$INSTALL_DIR/assets/fonts/Nasalization Rg.otf"
chmod +x "$INSTALL_DIR/celestial_scale.py" "$INSTALL_DIR/calibrate.py"
chown -R "$USER_NAME:$USER_NAME" "/home/$USER_NAME"

# 7. Sudoers, Env & Linger
echo "${USER_NAME} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/oas-nopasswd
chmod 440 /etc/sudoers.d/oas-nopasswd
echo "XDG_RUNTIME_DIR=/run/user/$(id -u $USER_NAME)" > /etc/celestial-env
mkdir -p /var/lib/systemd/linger
touch /var/lib/systemd/linger/"$USER_NAME"

# 8. Disable cloud-init (prevents first-run user-rename wizard)
mkdir -p /etc/cloud
touch /etc/cloud/cloud-init.disabled

# 9. Masking (TTY/DRM conflicts + unnecessary services)
for SVC in \
    getty@tty1 \
    plymouth-quit plymouth-quit-wait \
    lightdm gdm \
    userconfig \
    ModemManager bluetooth \
    cups cups-browsed \
    avahi-daemon colord polkit \
    dpkg-db-backup apt-daily apt-daily-upgrade \
    man-db e2scrub_reap \
    NetworkManager NetworkManager-wait-online \
    NetworkManager-dispatcher NetworkManager-sleep \
    rpi-eeprom-update raspi-config; do
    ln -sf /dev/null "/etc/systemd/system/${SVC}.service" || true
done

for TIMER in apt-daily apt-daily-upgrade man-db e2scrub_reap dpkg-db-backup logrotate fstrim; do
    ln -sf /dev/null "/etc/systemd/system/${TIMER}.timer" || true
done

# 10. rfkill unblock WiFi at boot
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

# 11. Networking
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

# 12. Activation
systemctl enable pigpiod ssh rfkill-unblock-wifi \
    systemd-networkd systemd-resolved \
    wpa_supplicant@wlan0.service celestial_scale.service
ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

echo "Bootstrap complete. The system is ready for flight."
