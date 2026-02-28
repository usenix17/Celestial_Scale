#Celestial Scale Calibration Guide

This document provides instructions on how to calibrate the load cell to ensure accurate planetary weight calculations.

##Prerequisites
- A Known Weight: You need a heavy, static object with a known weight in pounds.

- Access: An SSH connection or a keyboard attached to the Raspberry Pi.

- Service Control: The main kiosk service must be stopped to free up GPIO pins.

## Calibration Steps
  1. Prepare the Environment
  Stop the background service to prevent hardware conflicts with the HX711 sensor:

  ```bash
  sudo systemctl stop celestial_scale.service
  ```

  2. Run the Calibration Tool
  Execute the calibration script:

  ```bash
  python3 calibrate.py
  ```

  3. Follow the Prompts

  - Zeroing: When prompted, ensure the scale platform is empty to capture the Zero Offset.

  - Loading: Place your known weight in the center of the scale.

  - Input: Enter the exact weight of the object in pounds when prompted by the script.

  4. Capture the Ratio
  The script will output a `SCALE_RATIO`/`CALIBRATION_RATIO` value. Note this number for the next step.

## Applying the Calibration
To save these changes, you must update the main application file:

Open the main script:

```bash
vim /home/oas/celestial_scale/celestial_scale.py
```

Locate the Config & Tuning section.

Find the `CALIBRATION_FACTOR` constant:

```python
# Replace the existing value with your new SCALE_RATIO
CALIBRATION_FACTOR = [Your_New_Ratio]
```
Save and exit.

### Restart and Verify
Restart the service to apply the new calibration:

```bash
sudo systemctl start celestial_scale.service
```

## Troubleshooting
- Inconsistent Readings: Ensure the scale is on a flat, hard surface.

- Negative Weights: If the weight decreases when load is applied, the A+ and A- wires on the HX711 may be swapped.

- Disconnected Sensor: Ensure pigpiod is running by using sudo systemctl start pigpiod.
