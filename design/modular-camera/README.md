# Nimbus modular camera — mechanical prototype R01

Printable modular chassis around the existing screen holder. Designed for the HackMIT Bambu A1 / P1S machines listed at <https://hardware.hackmit.org/3d-prints>: 256 × 256 × 256 mm, 0.4 mm nozzle, PLA (checked September 19, 2026). **Nothing has been submitted for printing.**

## What this is

A roomy, serviceable assembly: rear screen frame, floor, two open sides, removable roof, replaceable webcam front, electronics shelf, webcam cradle, battery cradle, and interchangeable sensor carriers. Separate parts join with through-bolts and nuts. These are **not snap-fit joints**. The old screen-holder R02 is reused unchanged; do not print another if you already have it.

Main shell: approximately **224 W × 188 D × 180 H mm**, before protruding sensors, fasteners, screen washers and cables. This prioritizes hackathon access and modularity over a compact polished camera. Open sides are deliberate cable/service openings, not a sealed or drop-resistant enclosure.

In scope:

- Rear touchscreen, using your measured body and mounting-tab dimensions.
- Raspberry Pi **4B** and Arduino UNO Q, both accommodated on one removable shelf. Either can be omitted. Mechanical accommodation does not establish their software roles or electrical compatibility.
- Anker **A110D** power bank, removable and strapped at the bottom.
- Logitech **C270**, retained with its original clip and adjustable straps/padding.
- Up to five external sensor/control carriers: proposed roof Buttons, Light, Movement; front Distance and thermal/spare. Only install modules you actually need.

Not included: ESP32 / BOX-3 (explicitly removed), internal ASUS, printer, TP-Link hub, or a speaker. ASUS can remain external/networked. The Logitech has a microphone, not a speaker; voice output needs a separate later provision.

## Files

- `nimbus-modular-camera.blend`: assembled editable Blender model, including clearly named reference hardware.
- `build.py`: parameterized Python construction source for Blender 5.2; millimetres. Run `blender -b --python build.py` from an extracted package. `inputs/` contains the original screen-frame/washer meshes needed to rebuild.
- `stl/`: **only printed parts**, individually exported in intended print orientation. No electronics are included in these STLs. Import at 100%, millimetres.
- `PRINT_MANIFEST.md`: exact copies and exported dimensions.
- `ASSEMBLY.md`: staged print and assembly instructions, fasteners and outstanding checks.
- `SOURCES.md`: sources, measurements and assumptions.
- `print-manifest.json`: per-part manifold, connected-solid, volume, triangle and dimension checks.
- `clearance-checks.json`: rigid-part and reference-envelope interference checks.
- `mounting-coordinates.json`: the board and screen interface coordinates.
- `assembled-front.png`, `assembled-rear.png`, `cutaway.png`, `exploded.png`: CAD renders, not photos of an assembled device. Colored blocks are approximate hardware envelopes; logos/text are reference-only and are not embossed in the printable meshes.

This is mesh-based CAD with a reproducible generator, not a STEP/B-rep model. Package includes the `.blend`, source, STLs, documentation, references and renders.

## Validation and limits

Every exported part passed checks for a single connected solid, zero non-manifold edges, positive volume, a nonempty binary STL and dimensions within 0.002 mm of the source mesh. Every individual part fits the published printer volume. Modeled mating parts and conservative hardware envelopes show no solid intersections above the script's 0.1 mm³ reporting threshold.

**Physical fit, load strength, cable bending, slicer behavior, print duration, cooling and the complete power budget are not validated.** The model is ready for a staged fit prototype, not an assertion that all hardware can be enclosed and battery-powered without further testing. Start with the screen frame and hole coupon, not an unattended full print batch.

## Electrical scope is unchanged

This enclosure does not solve power distribution. The photographed A110D label gives a combined **5 V / 3 A (15 W)** output limit in multi-port use. Do not infer the complete Pi + UNO Q + screen + camera system can run from it merely because all components fit. Leave wall-power options accessible and test the intended wiring under load before relying on battery operation. No improvised battery wiring, GPIO power feeds or USB power backfeeding are part of this design.

Boards need insulating spacers, connectors need strain relief, and the power bank must remain removable without being squeezed. Keep ventilation clear. Do not use this prototype as a load-bearing shoulder-strap case until the assembled joints have been checked under the actual weight.
