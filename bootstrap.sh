#!/bin/bash
# ==============================================================================
# Script Name: bootstrap.sh
# Description: Automated install for Celestial Scale using GitHub sources.
# ==============================================================================

set -e

# --- Configuration ---
REPO_RAW="https://raw.githubusercontent.com/usenix17/Celestial_Scale/main"
INSTALL_DIR="/home/oas/celestial_scale"
USER_NAME="oas"

echo "Starting Celestial Scale Bootstrap from GitHub..."

# 1. System Dependencies
echo "Installing system packages..."
apt-get update
apt-get install -y \
    python3-pip python3-pygame pigpio python3-pigpio \
    python3-gpiozero git curl wget

# 2. Directory Setup
mkdir -p "$INSTALL_DIR"
# Create assets folder if your code expects it for fonts
mkdir -p "$INSTALL_DIR/assets/fonts"

# 3. Download Source Files
echo "Fetching source files from GitHub..."
curl -L "$REPO_RAW/celestial_scale.py" -o "$INSTALL_DIR/celestial_scale.py"
curl -L "$REPO_RAW/calibrate.py" -o "$INSTALL_DIR/calibrate.py"
curl -L "$REPO_RAW/celestial.service" -o "/etc/systemd/system/celestial_scale.service"
curl -L "$REPO_RAW/assets/fonts/Nasalization%20Rg.otf" -o "$INSTALL_DIR/assets/fonts/Nasalization Rg.otf"

# Ensure the scripts are executable
chmod +x "$INSTALL_DIR/celestial_scale.py"
chmod +x "$INSTALL_DIR/calibrate.py"

# 4. Permissions
# Ensure the 'oas' user owns the project files
chown -R $USER_NAME:$USER_NAME "$INSTALL_DIR"

# 5. Sudoers rule for safe shutdown from the kiosk button
echo "${USER_NAME} ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff" \
    > /etc/sudoers.d/celestial-poweroff
chmod 440 /etc/sudoers.d/celestial-poweroff

# 6. Enable Services
echo "Configuring services..."
systemctl enable pigpiod
systemctl enable celestial_scale.service

# 6. Kiosk Hardware Optimization
echo "Applying Pi Zero performance tweaks..."
CONFIG_PATH="/boot/firmware/config.txt"
[ ! -f "$CONFIG_PATH" ] && CONFIG_PATH="/boot/config.txt"

# Ensure display doesn't sleep and remove splash
{
    echo "disable_splash=1"
    echo "hdmi_force_hotplug=1"
    echo "dtparam=audio=on"
} >> "$CONFIG_PATH"

echo -e "\n========================================="
echo "  BOOTSTRAP COMPLETE"
echo "  Files downloaded to: $INSTALL_DIR"
echo "  Service installed from GitHub source."
echo "========================================="
