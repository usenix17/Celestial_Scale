#!/bin/bash
# ==============================================================================
# Script Name: pi-chroot.sh
# Description: Mounts a Raspberry Pi SD card and enters a QEMU-powered chroot.
# ==============================================================================

set -e

# --- 1. Root & Dependency Check ---
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root (sudo)."
    exit 1
fi

echo "Checking dependencies..."
# Required packages for partition management and ARM emulation
DEPS="qemu-user-static binfmt-support parted e2fsck resize2fs mount"
MISSING_DEPS=""

for dep in $DEPS; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        MISSING_DEPS="$MISSING_DEPS $dep"
    fi
done

if [ -n "$MISSING_DEPS" ]; then
    echo "Missing: $MISSING_DEPS. Installing via apt..."
    apt-get update && apt-get install -y $MISSING_DEPS
fi

# --- 2. Configuration & Input ---
read -p "Target Device (e.g., sda, sdb): " DEV_INPUT
DEV="/dev/${DEV_INPUT}"

if [ ! -b "$DEV" ]; then
    echo "Error: Device ${DEV} not found."
    exit 1
fi

# Show user the current partition table
echo -e "\nPartitions on ${DEV}:"
lsblk "$DEV"
echo ""

# Define partition paths (assuming standard RPi layout)
# Handles both /dev/sda1 and /dev/mmcblk0p1 naming conventions
if [[ "$DEV" == *"/dev/mmcblk"* ]] || [[ "$DEV" == *"/dev/nvme"* ]]; then
    PART1="${DEV}p1"
    PART2="${DEV}p2"
else
    PART1="${DEV}1"
    PART2="${DEV}2"
fi

if [ ! -b "$PART1" ] || [ ! -b "$PART2" ]; then
    echo "Error: Could not find boot (${PART1}) or root (${PART2}) partitions."
    exit 1
fi

# Architecture selection
read -p "Architecture [arm64/armhf] (default: arm64): " ARCH
ARCH=${ARCH:-arm64}

if [ "$ARCH" = "arm64" ]; then
    QEMU_BIN="/usr/bin/qemu-aarch64-static"
elif [ "$ARCH" = "armhf" ]; then
    QEMU_BIN="/usr/bin/qemu-arm-static"
else
    echo "Error: Unsupported architecture ${ARCH}."
    exit 1
fi

# --- 3. Mount Point Setup ---
MOUNTPOINT="/mnt/pi_root"
mkdir -p "$MOUNTPOINT"

if mountpoint -q "$MOUNTPOINT"; then
    echo "Error: ${MOUNTPOINT} is already in use. Please unmount it first."
    exit 1
fi

# --- 4. Cleanup Logic (The Trap) ---
# This ensures that even if you hit Ctrl+C, the script cleans up after itself.
cleanup() {
    echo -e "\n[Cleaning up mounts...]"
    umount -l "${MOUNTPOINT}/dev/pts" 2>/dev/null || true
    umount -l "${MOUNTPOINT}/dev" 2>/dev/null || true
    umount -l "${MOUNTPOINT}/proc" 2>/dev/null || true
    umount -l "${MOUNTPOINT}/sys" 2>/dev/null || true
    # Unmount boot first, then root
    if [ -d "$BOOT_MOUNT" ]; then umount -l "$BOOT_MOUNT" 2>/dev/null || true; fi
    umount -l "$MOUNTPOINT" 2>/dev/null || true
    echo "Done. Safe to remove device."
}
trap cleanup EXIT

# --- 5. Partition Resizing (Optional) ---
read -p "Expand root partition to fill remaining space? [y/N]: " EXPAND
if [[ "$EXPAND" =~ ^[Yy]$ ]]; then
    echo "Resizing ${PART2}..."
    parted -s "$DEV" resizepart 2 100%
    e2fsck -f -p "$PART2" || true # -p is 'preen' (auto-fix)
    resize2fs "$PART2"
fi

# --- 6. Mounting Process ---

echo "Mounting Root (${PART2})..."
mount "$PART2" "$MOUNTPOINT"

# Detect correct boot path (Legacy vs Bookworm)
if [ -d "${MOUNTPOINT}/boot/firmware" ]; then
    BOOT_MOUNT="${MOUNTPOINT}/boot/firmware"
else
    BOOT_MOUNT="${MOUNTPOINT}/boot"
fi

echo "Mounting Boot (${PART1}) to ${BOOT_MOUNT}..."
mount "$PART1" "$BOOT_MOUNT"

echo "Binding system filesystems..."
mount --bind /dev "${MOUNTPOINT}/dev"
mount --bind /dev/pts "${MOUNTPOINT}/dev/pts"
mount --bind /proc "${MOUNTPOINT}/proc"
mount --bind /sys "${MOUNTPOINT}/sys"

# Ensure QEMU is inside the chroot to allow ARM execution
cp "$QEMU_BIN" "${MOUNTPOINT}/usr/bin/"

# Enable SSH flag on boot partition if missing
if [ ! -f "${BOOT_MOUNT}/ssh" ] && [ ! -f "${BOOT_MOUNT}/ssh.txt" ]; then
    touch "${BOOT_MOUNT}/ssh"
    echo "SSH enabled (created empty file in boot)."
fi

# --- 7. Enter Chroot ---
echo -e "\n========================================="
echo "  SUCCESS: Entering Pi Chroot environment"
echo "  Target: ${ARCH} on ${DEV}"
echo "  Type 'exit' or press Ctrl+D to finish."
echo "=========================================\n"

chroot "$MOUNTPOINT" /bin/bash

# Script exits here, triggering the 'cleanup' trap defined above.
