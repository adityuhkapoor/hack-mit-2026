# Dimensions: sources versus allowances

Checked September 19, 2026. Manufacturer mechanical drawings informed the computer mounting holes; user measurements take precedence for the unconfirmed screen assembly. Product envelope dimensions are not detailed port, clip or cable geometry.

| Component | Basis used | Confidence / remaining issue |
|---|---|---|
| Touchscreen | User: 6.5 in / 165.1 mm long, final short-side measure about 100.5 mm, 124 mm including ears, holes about 156 × 115 mm; tab holes less than 3 mm | Approximate physical measurements. Generous opening and slots compensate; test fit required. Rear 20 mm keepout is a design allowance, not verified rear-stack maximum. |
| Raspberry Pi 4B | 85 × 56 mm PCB; 58 × 49 mm hole pitch with asymmetric long-edge placement | Official drawing. Model uses original 3.5 mm edge offsets and rotates board 180°. Cooler height/actual plugs not measured. |
| UNO Q | 68.58 × 53.34 mm PCB; four individual hole coordinates | Official drawing, not a guessed rectangular pattern. 25 mm vertical allowance is not measured height. |
| Logitech C270 | W72.91 × D66.64 × H31.91 mm including clip | Official product sheet envelope. Articulated clip changes pose; padded/strapped adjustable support, no precision fitted lens ring. |
| Anker A110D | 140.7 × 71.7 × 15.4 mm | Manufacturer model page. Leads/plug bends are additional. Multi-output power limit taken from photographed actual label. |
| Modulino-style sensors | Example 41 × 25.36 mm PCB, nominal 32 × 16 mm holes | Buttons drawing provides hole footprint. Repeated reference rectangles are planning envelopes, not exact models for all five boards. Thermal breakout footprint unverified. |
| Print volume/material | 256 mm cubed, Bambu A1 and P1S, 0.4 mm nozzle, PLA | Actual authenticated HackMIT 3D print page; no job submitted or sliced. |

## Primary references

- Pi 4 mechanical drawing: <https://pip-assets.raspberrypi.com/categories/545-raspberry-pi-4-model-b/documents/RP-008343-DS-1-raspberry-pi-4-mechanical-drawing.pdf>
- UNO Q datasheet, mechanical drawing: <https://docs.arduino.cc/resources/datasheets/ABX00162-datasheet.pdf>
- Logitech C270 product sheet: <https://www.logitech.com/assets/46735/4/hd-webcam-c270.pdf>
- Anker A110D manufacturer product page: <https://anker.com.sg/products/anker-zolo-powerbank-10000mah-22-5w-power-bank-fast-charging-compact-portable-charger-with-built-in-usb-c-cable-a110d>
- Modulino Buttons: <https://docs.arduino.cc/resources/datasheets/ABX00110-datasheet.pdf>
- Modulino Light: <https://docs.arduino.cc/resources/datasheets/ABX00111-datasheet.pdf>
- Modulino Movement: <https://docs.arduino.cc/resources/datasheets/ABX00101-datasheet.pdf>
- Modulino Distance: <https://docs.arduino.cc/resources/datasheets/ABX00102-datasheet.pdf>
- HackMIT 3D printing: <https://hardware.hackmit.org/3d-prints>

Screen listing found earlier: <https://www.amazon.com/dp/B0BRMWMK6X>. Listing image: <https://m.media-amazon.com/images/I/61lyOJbuF-L._AC_SL1247_.jpg>. These do **not** replace a verified mechanical drawing of your unit; screen orientation and the right-side connectors were confirmed from your physical photos (IMG_7714–7716).

## Exact board-hole convention

Pi drawing: board X = 0…85, Y = 0…56. Holes at X = 3.5, 61.5 and Y = 3.5, 52.5 mm. The CAD translates and rotates this pattern with the board; see `mounting-coordinates.json` for assembly coordinates.

UNO Q drawing, coordinates from left/top board edges in millimetres:

- (15.24, 2.54)
- (13.97, 50.80)
- (66.04, 17.78)
- (66.04, 45.72)

The generator converts this top-edge convention to Cartesian Y and rotates the board 180°. The footprint is not a symmetric four-corner pattern.

## Assumptions deliberately visible

- Assembly uses through-bolts/nuts, not embedded inserts or untested printed snap hooks.
- Rigid envelope checking excludes cables, fasteners, tiny board components, connector protrusion and articulated clip geometry.
- Component volumes are planning blocks; renders are not scan-derived replicas.
- Screen slots accept approximate alignment; they do not make large screws safe for small PCB tab holes.
- Neither a housing fit nor a supplier power rating demonstrates that the complete device is powered correctly. The Anker's combined 15 W label limit remains an independent integration constraint.
