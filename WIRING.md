```

POWER DISTRIBUTION (star topology)
=====================================

                    ┌──────────────────────────┐
                    │   MEANWELL 5V PSU        │
                    └────────┬────────┬────────┘
                             │+       │-
                      [INLINE FUSE]   │
                             │        │
                    ┌────────┴──┐  ┌──┴────────┐
                    │ +5V       │  │ GND       │
                    │ BUS BAR   │  │ BUS BAR   │
                    └──┬──┬──┬──┘  └──┬──┬──┬──┘
                       │  │  │        │  │  │
              ┌────────┘  │  └──────┐ │  │  │
              │           │         │ │  │  │
              ▼           ▼         │ │  │  │
       micro-USB       HX711 VCC    │ │  │  │
    ┌──[polyfuse]──┐                │ │  │  │
    │  PI ZERO 2W  │                │ │  │  │
    └──────────────┘◄───────────────┘ │  │  │
       HDMI Screen ◄──────────────────┘  │  │
       HX711 GND   ◄─────────────────────┘  │
       Screen GND  ◄────────────────────────┘


SIGNAL WIRING
=====================================

                    ┌──────────────┐
                    │  HDMI SCREEN │
                    └──────┬───────┘
                           │ mini HDMI
                           │
             micro-USB ┌───┴───────────────────────────────┐
             from bus  │  RASPBERRY PI ZERO (2W)           │
                       │                                   │
                       │  Pin 29 (GPIO 5)  ───────┐        │
                       │  Pin 31 (GPIO 6)  ────┐  │        │
                       │  Pin 12 (GPIO 18) ─┐  │  │        │
                       │  Pin 14 (GND) ──┐  │  │  │        │
                       └─────────────────┼──┼──┼──┼────────┘
                                         │  │  │  │
            TARE BUTTON                  │  │  │  │
            ┌──────┐                     │  │  │  │
            │ ○  ○ │                     │  │  │  │
            └─┤──┤─┘                     │  │  │  │
              │  │                       │  │  │  │
              │  └───────────────────────┘  │  │  │
              └─────────────────────────────┘  │  │
                                               │  │
                              ┌────────────────┴──┴──────────────┐
                              │  SPARKFUN HX711 (Load Cell Amp)  │
                              │                                  │
                              │  SCK  ◄── GPIO 6                 │
                              │  DOUT ──► GPIO 5                 │
                              │  GND  ◄── GND bus bar            │
                              │  VCC  ◄── +5V bus bar            │
                              │                                  │
                              │  E+  ──┐                         │
                              │  E-  ──┼──┐                      │
                              │  A+  ──┼──┼──┐                   │
                              │  A-  ──┼──┼──┼──┐                │
                              └────────┼──┼──┼──┼────────────────┘
                                       │  │  │  │
                              ┌────────┼──┼──┼──┼────────────────┐
                              │  SPARKFUN LOAD CELL COMBINER     │
                              │  (passive — traces only,         │
                              │   no components)                 │
                              │                                  │
                              │  E+  ◄─┘  │  │  │                │
                              │  E-  ◄────┘  │  │                │
                              │  A+  ◄───────┘  │                │
                              │  A-  ◄──────────┘                │
                              │                                  │
                              │  LC1   LC2    LC3  LC4           │
                              └──┬──┬──┬──┬──┬──┬──┬──┬──────────┘
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


LOAD CELL WIRING (each cell has 4 wires):
==========================================
Each load cell connects to the combiner board.
Match wire colors to the combiner's labeled terminals.
Typical color coding:
  RED   = E+ (Excitation+)
  BLACK = E- (Excitation-)
  WHITE = S+ (Signal+)
  GREEN = S- (Signal-)  [may be bare/shield wire on some cells]


PI ZERO GPIO HEADER REFERENCE (relevant pins only):
=====================================================

              3.3V  (1)  ○ ○  (2)  5V
                    (3)  ○ ○  (4)  5V
                    (5)  ○ ○  (6)  GND
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
                    (39) ○ ○  (40)


DECOUPLING CAPACITORS:
======================
Place all caps as close to the component's power pins as possible.
Electrolytic caps are polarized — observe polarity (+ to VCC, - to GND).

  Bus bar (at the bar itself):
    1 × 470 µF electrolytic       across +5V and GND rails

  Raspberry Pi (at micro-USB power input):
    1 × 100 µF electrolytic       across +5V and GND
    1 × 0.1 µF ceramic            across +5V and GND

  HDMI Screen (at power input connector):
    1 × 100 µF electrolytic       across +5V and GND
    1 × 0.1 µF ceramic            across +5V and GND

  HX711 (MOST IMPORTANT — at VCC and GND pins):
    1 × 10–47 µF electrolytic     across VCC and GND
    1 × 0.1 µF ceramic            across VCC and GND


SUMMARY OF CONNECTIONS:
========================
  Meanwell PSU +5V  ──[inline fuse]──► +5V bus bar
  Meanwell PSU GND  ──────────────────► GND bus bar

  +5V bus bar ──► Pi micro-USB (via cable; Pi has onboard polyfuse)
  +5V bus bar ──► HDMI Screen
  +5V bus bar ──► HX711 VCC
  GND bus bar ──► Pi GND (via micro-USB cable)
  GND bus bar ──► Screen GND
  GND bus bar ──► HX711 GND

  Pi mini-HDMI        ──► HDMI Screen
  Pi Pin 29 (GPIO 5)  ──► HX711 DOUT
  Pi Pin 31 (GPIO 6)  ──► HX711 SCK
  Pi Pin 12 (GPIO 18) ──► Tare Button (one leg)
  Pi Pin 14 (GND)     ──► Tare Button (other leg)
```
