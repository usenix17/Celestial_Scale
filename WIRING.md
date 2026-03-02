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
       HX711/NAU7802 GND ◄───────────────┘  │
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
                              │         or NAU7802               │
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


LOAD CELL WIRING (each cell has 3 wires):
==========================================
Each single-point load cell connects to the combiner board's +, -, and C terminals.
The combiner wires all four into a Wheatstone bridge configuration.

Typical color coding (verify with a multimeter — colors can vary by manufacturer):
  RED   = + (Excitation)
  BLACK = - (Excitation)
  WHITE = C (Center tap / signal)

  ⚠ To confirm which wire is C: measure resistance between all three pairs.
    The two highest-resistance pairs share the C wire.

COMBINER → HX711/NAU7802 WIRING (5 wires out):
=============================================
  RED    → E+ (Excitation+)
  BLACK  → E- (Excitation-)
  WHITE  → A+ (Signal+)
  GREEN  → A- (Signal-)
  YELLOW → GND / shield (optional, see CAT5e note below)


CAT5e WIRING — TWO CABLE RUNS
==============================
Using CAT5e puts each differential pair on a twisted pair, keeping
common-mode noise out of both the analog bridge and the digital lines.
Two separate cable runs are used — one analog, one digital — so clock
pulses on the digital run can never couple into the millivolt-level
bridge signal.

  CABLE A: Combiner Board → ADC  (analog path — most sensitive)
  ──────────────────────────────────────────────────────────────
  Combiner    CAT5e color     Function          Twisted with
  ──────────  ──────────────  ────────────────  ──────────────
  E+ (Red)    Orange          Excitation +      Orange-White
  E- (Black)  Orange-White    Excitation -      Orange
  S+ (White)  Blue            Signal +          Blue-White
  S- (Green)  Blue-White      Signal -          Blue

  The YELLOW shield wire from the combiner is left unconnected at
  the ADC end and tied to GND at the combiner end only (single-point
  grounding prevents a ground loop through the shield).

  CABLE B: ADC → Raspberry Pi  (digital path)
  ──────────────────────────────────────────────────────────────
  ADC pin     CAT5e color     Function          Twisted with
  ──────────  ──────────────  ────────────────  ──────────────
  VCC         Brown           5V power          Brown-White
  GND         Brown-White     System ground     Brown
  DT / SDA    Green           Data (GPIO 5)     Green-White
  SC / SCL    Green-White     Clock (GPIO 6)    Green

  Clock and data are twisted together so any noise induced on the
  cable appears identically on both lines; the Pi's logic threshold
  rejects the common-mode component.


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
   AMP DOUT GPIO 5  (29) ○ ○  (30)
   AMP SCK  GPIO 6  (31) ○ ○  (32)
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
    1 × 0.1 µF ceramic            across VCC and GND

  HX711/NAU7802 (MOST IMPORTANT — at VCC and GND pins):
    1 × 10–47 µF electrolytic     across VCC and GND
    1 × 0.1 µF ceramic            across VCC and GND


SUMMARY OF CONNECTIONS:
========================
  Meanwell PSU +5V  ──[inline fuse]──► +5V bus bar
  Meanwell PSU GND  ──────────────────► GND bus bar

  +5V bus bar ──► Pi micro-USB (via cable; Pi has onboard polyfuse)
  +5V bus bar ──► HDMI Screen
  +5V bus bar ──► HX711/NAU7802 VCC
  GND bus bar ──► Pi GND (via micro-USB cable)
  GND bus bar ──► Screen GND
  GND bus bar ──► HX711/NAU7802 GND

  Pi mini-HDMI        ──► HDMI Screen
  Pi Pin 29 (GPIO 5)  ──► HX711/NAU7802 DOUT
  Pi Pin 31 (GPIO 6)  ──► HX711/NAU7802 SCK
  Pi Pin 12 (GPIO 18) ──► Tare Button (one leg)
  Pi Pin 14 (GND)     ──► Tare Button (other leg)
```
