# Print and assemble in stages

## Before the first plate

1. Confirm the physical parts match the listed **Pi 4B, UNO Q, C270 and Anker A110D**. A different revision, fitted Pi case or taller cooler changes the fit.
2. Obtain through-bolts, matching nuts and washers. HackMIT's inventory listed an M3 assortment but **0/1 available** when checked; availability is not possession. Do not start all structural prints assuming fasteners will appear.
3. Print `15_fastener_fit_coupon` and `01_rear_screen_frame`. If the screen-holder R02 is already printed, reuse it instead. Coupon holes are 2.9, 3.4, 3.6 and 3.8 mm, in order from one end to the other; distinguish ends by the visibly smallest hole. Test your actual bolts without forcing them.
4. Place the unpowered screen against the frame. Confirm all four tab positions fall within the slots, screen edges do not press against the plastic, and right-side HDMI/USB plugs can be inserted. Use the small tab screws/washer arrangement below; **do not drive M3 screws into the roughly 3 mm screen tab holes by force**.

## Print settings — starting point, not a sliced job

- Use the site's PLA / 0.4 mm nozzle configuration. Start at 0.20 mm layers, four perimeters, five top/bottom layers and about 20–25% infill; staff may adjust for their filament/printer.
- STLs are already oriented flat on Z=0. Sides print with their outside faces on the bed and attachment lugs upward. Cradles print with their flat undersides down.
- Inspect every layer preview, especially the horizontal small bolt bores in the side lugs. Use local supports only if the slicer/operator judges them necessary; do not indiscriminately fill all slots with support.
- Largest part is 224 mm across, within the 256 mm bed. Allow room for brim/purge/adhesion features and accept the slicer's actual printable-area check.
- Avoid stacking parts or changing scale to fit. Print time, filament mass and number of plates require actual slicing; none are claimed here.
- Tiny spacers may need brims. Purchased insulating spacers of the same height can replace printed ones after checking outside clearance.

## Fasteners and straps

All major chassis bores are **3.4 mm clearance for M3 through-bolts**, not modeled threads. Nuts remain accessible through the open side frames. Use washers and tighten gently; PLA creeps and excessive tightening cracks thin features.

| Connection | Quantity | Starting fastener choice; verify stack before tightening |
|---|---:|---|
| Floor to side lugs | 6 | M3 × 16 mm + nuts/washers |
| Roof to side lugs | 6 | M3 × 16 mm + nuts/washers |
| Rear screen FRAME to side lugs | 4 | M3 × 16 mm + nuts/washers; separate from screen PCB tabs |
| Front panel to side lugs | 4 | M3 × 16 mm + nuts/washers |
| Electronics shelf to side ledges | 4 | M3 × 12–16 mm + nuts/washers |
| Webcam shelf to side ledges | 4 | M3 × 12–16 mm + nuts/washers |
| Battery cradle to floor | 4 | M3 × 12–16 mm + nuts/washers |
| Sensor carrier to roof/front | 4 per carrier, up to 20 | M3 × 12–16 mm + nuts/washers |
| Pi board through 6 mm spacers/shelf | 4 | M2.5 × 14–16 mm + nuts/insulating washers |
| UNO Q board through 6 mm spacers/shelf | 4 | M3 × 14–16 mm + nuts/insulating washers |
| Sensor PCB through 5 mm spacers/carrier | Up to 4 per PCB | Usually M3 × 14–16 mm **only if actual holes accept it**; otherwise insulated strap mounting |
| Screen tabs to rear frame | 4 | M2 or M2.5 with printed tab washers and nuts; select length after checking actual tab stack, not nominal M3 |

Structural subtotal with all five carriers: **52 M3 bolt/nut pairs**, plus 4 for UNO Q and up to 20 for sensor boards. Pi and screen use separate smaller hardware. An assortment of lengths is more useful than buying one length blindly; no bolt end may contact an electronic component or connector.

Bring two soft straps for the battery and one or two for the webcam clip, non-slip padding, and small ties for cable strain relief. Straps must fit the 4 mm-wide slot openings. Thin zip ties can locate parts temporarily, but are not a tested substitute for structural fasteners. Do not tie across the camera lens/microphone, battery ports or PCB components.

## Assembly sequence

1. **Prepare floor and battery cradle.** Feed the battery straps through the matching slots in BOTH pieces before fastening the cradle to the floor. Screw holes are on a 152 × 84 mm rectangle. Put the Anker between the low guides with controls/ports reachable. Strap gently; no adhesive is needed on the battery itself. Leave it disconnected during assembly.
2. **Attach the two side frames to the floor.** The long inward ledges belong inside; rear is the screen side. Floor is below its lugs, roof above its lugs. Leave bolts slightly loose until the panels are squared.
3. **Build the electronics shelf on the bench.** Four 6 mm spacers under each board; Pi's 2.9 mm holes use M2.5, UNO Q's 3.4 mm holes use M3. The patterns are intentionally different. Pi large USB/Ethernet ports point toward the left service opening; UNO Q USB-C points toward the right. Test connectors and underside clearance before fastening the shelf onto the lower ledges. Keep small parts away from powered boards.
4. **Fit webcam shelf.** Fasten it to the upper ledges. Place the intact C270/clip between its guide walls, use non-slip padding and adjustable straps. Its face is near the front opening and the USB cable routes rearward/sideways. Camera clip angle and optical aim are physical fit checks, not determined by the placeholder box in the render. Secure the clip/base, never the optical face.
5. **Mount screen to its separate frame**, gently fastening through the elongated tab slots with washers. No load should press on glass, flex cables or display components. Plug in its cables while access is open, then fasten the FRAME's four outer chassis holes to the rear lugs. The screen PCB tab screws do not join the chassis.
6. **Add front and sensor carriers.** Front panel bolts to four lugs. Optional front carriers can hold Distance and thermal/spare boards; keep their optical/thermal sensing faces exposed. Generic slots do not certify an unknown module's mounting pattern. Route cables through the center opening with insulating spacers and strain relief.
7. **Wire/test before adding roof.** Capture a picture, operate the screen, and verify selected control/sensor data using the intended computer. Check every USB/HDMI connector can be removed without dismantling a board. Do not power both computers or backfeed ports using unverified splitter wiring.
8. **Fit roof last.** Suggested carrier locations: Buttons (front-left), Light (front-right), Movement (rear-right). Install only needed carriers and connect their cables before closing. The buttons themselves remain accessible on the real PCB; no precision plunger mechanism is required.
9. **Bench test under load.** Confirm no power resets, screen dropouts, hot trapped cables, fan obstructions or battery stress. Test lifting gently over a table; strap slots are not proof of carrying strength.

## Interfaces retained for future changes

- Screen frame outside: 192 × 140 × 4 mm; outer chassis pitch **182 × 128 mm**.
- Screen-tab slot nominal pitch **156 × 115 mm**, each slot 10 × 5 mm; central opening 172 × 106 mm. Based on your measurements, not a manufacturer-certified screen drawing.
- Front is removable independently of the webcam shelf; aperture is 94 × 54 mm. Swap only this panel for later styling.
- Each sensor carrier is 70 × 46 × 3 mm, outer attachment pitch **60 × 34 mm**. PCB slots accommodate a nominal 32 × 16 mm four-hole footprint with horizontal adjustment; other modules can use the tie slots with insulation.
- Both computer footprints are already present on the shelf; unused space can remain empty. A USB hub is not implicitly mounted inside this design.

## Remaining physical checks

These are practical fit tests, not requests to measure every component precisely:

- Screen tab fit and thickness, washer seating and real connector bend radius.
- C270 clip pose, stable aim, lens/microphone unobstructed, safe cable exit.
- Pi fitted cooling assembly within the allocated **37 mm above PCB bottom**; UNO Q within the allocated **25 mm**. These are design allowances, not measured manufacturer heights.
- Power-bank port orientation and built-in lead routing; battery can slide out after straps are released.
- Actual sensor board hole patterns, exposed sensing direction, and enough cable length to remove the roof without yanking connectors.
- Fastener access/protrusion and print shrinkage. Relieve only plastic as needed, not holes in electronics.
- Full operating power and temperature test before expecting portable standalone operation.
