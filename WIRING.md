```

                    ┌──────────────┐
                    │  HDMI SCREEN │
                    └──────┬───────┘
                           │ mini HDMI
                           │
    USB-C power   ┌────────┴──────────────────────────────┐
 Red───────────┐  │  RASPBERRY PI ZERO (2W)               │
               |  │                                       │
               |  │  Pin 2  (5V)  ──────────────────┐     │
               └──|--Pin 4  (5V)                    |     |
 Black─────────┐  │  Pin 6  (GND) ──────────────┐   │     │
               |  │  Pin 29 (GPIO 5)  ───────┐  │   │     │
               |  │  Pin 31 (GPIO 6)  ────┐  │  │   │     │
               |  │  Pin 12 (GPIO 18) ─┐  │  │  │   │     │
               |  │  Pin 14 (GND) ──┐  │  │  │  │   │     │
               └──|--Pin 39 (GND)   |  |  |  |  |   |     |
                  └─────────────────┼──┼──┼──┼──┼───┼─────┘
                                    │  │  │  │  │   │
                                    │  │  │  │  │   │
          TARE BUTTON               │  │  │  │  │   │
          ┌──────┐                  │  │  │  │  │   │
          │ ○  ○ │                  │  │  │  │  │   │
          └─┤──┤─┘                  │  │  │  │  │   │
            │  │                    │  │  │  │  │   │
            │  └────────────────────┘  │  │  │  │   │
            └──────────────────────────┘  │  │  │   │
                                          │  │  │   │
                              ┌───────────┴──┴──┴───┴───┐
                              │  SPARKFUN HX711         │
                              │  (Load Cell Amp)        │
                              │                         │
                              │  SCK  ◄── GPIO 6        │
                              │  DOUT ──► GPIO 5        │
                              │  GND  ◄── Pi GND        │
                              │  VCC  ◄── Pi 5V         │
                              │                         |
                              │  E+  ──┐                |
                              │  E-  ──┼──┐             │
                              │  A+  ──┼──┼──┐          │
                              │  A-  ──┼──┼──┼──┐       │
                              └────────┼──┼──┼──┼───────┘
                                       │  │  │  │
                              ┌────────┼──┼──┼──┼────────┐
                              │  SPARKFUN LOAD CELL      │
                              │  COMBINER                │
                              │                          │
                              │  E+  ◄─┘  │  │  │        │
                              │  E-  ◄────┘  │  │        │
                              │  A+  ◄───────┘  │        │
                              │  A-  ◄──────────┘        │
                              │                          │
                              │  LC1   LC2    LC3  LC4   │
                              └──┬──┬──┬──┬──┬──┬──┬──┬──┘
                                 │  │  │  │  │  │  │  │
                    ┌────────────┘  │  │  │  │  │  │  └───────────────┐
                    │  ┌────────────┘  │  │  │  │  └───────────────┐  │
                    │  │      ┌────────┘  │  │  └────────────┐     │  │
                    │  │      | ┌─────────┘  └─────────────┐ │     │  │
                    │  │      | │                          │ │     │  │
               ┌────┴──┴───┐  | │                          │ | ┌───┴──┴────┐
               │  LOAD CELL│  | |                          | | | LOAD CELL │
               │     #1    │  | |                          | | |  #4       │
               └───────────┘  | |                          | | └───────────┘
                    ┌─────────┘ |                          | |
               ┌────┘────────┐──┘                   ┌──────┘─└────┐
               │  LOAD CELL  │                      │  LOAD CELL  │
               │     #2      │                      │     #3      │
               └─────────────┘                      └─────────────┘


LOAD CELL WIRING (each cell has 3 wires):
==========================================
Each load cell connects to the combiner board.
Match wire colors to the combiner's labeled terminals.
Typical color coding:
  RED   = E+ (Excitation+)
  BLACK = E- (Excitation-)
  WHITE = Signal (S+ or S-)



PI ZERO GPIO HEADER REFERENCE (relevant pins only):
=====================================================

              3.3V  (1)  ○ ○  (2)  5V  ◄── to HX711 VCC
                    (3)  ○ ○  (4)  5V  ◄── to USB C Red
                    (5)  ○ ○  (6)  GND ◄── to HX711 GND
                    (7)  ○ ○  (8)
              GND   (9)  ○ ○  (10)
                    (11) ○ ○  (12) GPIO 18 ◄── TARE BUTTON
                    (13) ○ ○  (14) GND     ◄── TARE BUTTON GND
                    (15) ○ ○  (16)
                    (17) ○ ○  (18)
                    (19) ○ ○  (20)
                    (21) ○ ○  (22)
                    (23) ○ ○  (24)
                    (25) ○ ○  (26)
                    (27) ○ ○  (28)
 HX711 DOUT GPIO 5  (29) ○ ○  (30)
 HX711 SCK  GPIO 6  (31) ○ ○  (32)
                    (33) ○ ○  (34)
                    (35) ○ ○  (36)
                    (37) ○ ○  (38)
 USB C GND          (39) ○ ○  (40)

SUMMARY OF CONNECTIONS:
========================
  Pi Pin 2  (5V)      ──► HX711 VCC
  Pi Pin 4  (5V)      ──► USB C Red
  Pi Pin 6  (GND)     ──► HX711 GND
  Pi Pin 29 (GPIO 5)  ──► HX711 DOUT
  Pi Pin 31 (GPIO 6)  ──► HX711 SCK
  Pi Pin 12 (GPIO 18) ──► Tare Button (one leg)
  Pi Pin 14 (GND)     ──► Tare Button (other leg)
  Pi Pin 39 (GND)     ──► USB C Black
  Pi micro-USB        ──► 5V Power Supply
  Pi mini-HDMI        ──► HDMI Screen
```
