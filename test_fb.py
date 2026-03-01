#!/usr/bin/env python3
"""Write solid color directly to /dev/fb0 to confirm the framebuffer is connected to the display."""

from pathlib import Path

virtual_size = Path("/sys/class/graphics/fb0/virtual_size").read_text().strip()
bpp = int(Path("/sys/class/graphics/fb0/bits_per_pixel").read_text().strip())
width, height = map(int, virtual_size.split(","))
bytes_per_pixel = bpp // 8

print(f"Framebuffer: {width}x{height}, {bpp}bpp ({bytes_per_pixel} bytes/pixel)", flush=True)

# Try red in BGRA and RGBA byte orders — one will be correct
pixel = bytes([0, 0, 255, 255][:bytes_per_pixel])

print(f"Writing to /dev/fb0...", flush=True)
with open("/dev/fb0", "wb") as f:
    f.write(pixel * (width * height))

print("Done — screen should be a solid color.", flush=True)
