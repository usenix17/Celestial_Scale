#!/usr/bin/env python3
"""Quick kmsdrm display test. Screen should turn solid red for 10 seconds."""

import os
import time

os.environ["SDL_VIDEODRIVER"] = "kmsdrm"

import pygame

pygame.display.init()
screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
print(f"Screen size: {screen.get_size()}", flush=True)

screen.fill((255, 0, 0))
pygame.display.flip()
print("Flipped — screen should be red", flush=True)

time.sleep(10)

pygame.quit()
print("Done", flush=True)
