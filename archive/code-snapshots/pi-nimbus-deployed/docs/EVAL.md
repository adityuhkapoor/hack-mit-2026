# Can a photo's look be reverse-engineered? What the numbers say

Measured on 2026-09-15. Scripts are in `pipeline/eval/`; photos are Unsplash images via Lorem Picsum
(`eval/credits.json`).

## Setup

"The look looks right" can't be scored, so the grade is made known:

1. Take a natural photo **A** and apply a hand-built grade **G**. Seven are used: teal-orange, warm film,
   cool matte, punchy warm, cross-process, selenium mono, and moody green (a hue-specific shift).
   **G(A)** is the "Instagram post".
2. Each method sees **only G(A)** and must produce a Look.
3. Apply that Look to a *different* photo **B**, and compare with the true **G(B)**.

The score is mean CIEDE2000 over pixels (lower is better; roughly 1 is invisible, 2–3 is noticeable side by
side). Photos never cross between the train and test roles. The neutralizer bias LUTs are learned on
held-out photos that no test pair uses.

## Results (7 grades × 6 photo pairs, `sweep_estimators.py --pairs 6`)

| Method | Mean ΔE2000 |
|---|---:|
| Do nothing (B unchanged) | 8.78 |
| **Oracle**: LUT fitted on the true pair (A, G(A)) | **0.89** ¹ |
| Classic color transfer (MKL in Lab, B → G(A)) | 18.50 ¹ |
| Stats neutralizer + free 3D LUT, full strength | 10.46 |
| Stats neutralizer + tone curves, full strength | 10.29 |
| klein neutralizer + 3D LUT, full strength | 14.07 |
| **Stats neutralizer + tone curves + debias, strength 0.5** (default, no GPU) | **7.92** |
| **klein neutralizer + 3D LUT + debias, strength 0.25** (`/refine`) | **7.76** |

¹ From `run_eval.py --pairs 3` (same grades and photos; 3 pairs).

Tier 1 restyle, where FLUX.2 klein edits the photo directly from the reference, was tested on one pair
(teal-orange, photo 76 from reference 75):

| Variant | ΔE raw | ΔE distilled to a LUT |
|---|---:|---:|
| Do nothing | 7.79 | — |
| Text description only | 27.5 | 20.5 |
| Reference shuffled into a content-free swatch | 22.5 | 13.9 |
| Reference + "don't copy objects" prompt | 21.6 | 14.7 |

## What it means

1. **Classic color transfer is worse than doing nothing** (18.5 vs 8.8). It copies the reference's
   *scene* colors: a golden field turns your flowers yellow. That is the failure people notice when "the
   filter doesn't look like the post".
2. **The LUT engine is essentially exact once the unedited original is known** (0.89). So the product
   takes a before/after pair when one exists (`POST /looks` with `before`). Preset sellers post exactly
   these before/after sliders on Instagram.
3. **Blind estimation from a single photo is genuinely ill-posed.** The best methods beat doing nothing
   by 10–12%, and only at partial strength. Three things were each necessary:
   - **A constrained model:** per-channel curves plus saturation, rather than a free 3D LUT, so
     scene-specific errors can't be memorized.
   - **Debiasing the neutralizer:** its systematic push is learned on ungraded photos and cancelled out.
   - **Shrinkage:** partial strength.
4. **Diffusion is a poor copier of looks.** FLUX.2 klein pushes every image toward high contrast and
   saturation, and with the raw reference it pastes reference objects into the photo. It is useful in
   two narrower roles: as a (debiased) neutralizer, and as the explicitly creative "Reimagine" tier.

## Honest caveats

- The grades are synthetic and moderate. Real Instagram looks also include local edits (masks, dodge
  and burn) that no global grade represents.
- ΔE punishes "too strong" and "too weak" equally. A user may prefer a visibly stronger look than the
  ΔE-optimal strength, which is why strength is a slider, not a constant.
- The sample is 42 test cases over 12 photos, so differences under about 0.2 ΔE are noise.
- The vision model (gemma3:4b) writes good descriptions but sometimes gives wrong shooting advice
  (e.g. "slower shutter speed to add grain").

## Live preview: how fast can diffusion actually go? (2026-09-15)

The question was whether a reimagine style can preview in realtime. Measured on the box (RTX 3060 Ti, 8 GB)
with the per-frame loop running *on* the box, so the network is excluded:

| Setup | ms/frame | fps |
|---|---:|---:|
| klein reference-edit, 0.15 MP, 4 steps | 4993 (remote, 5 round trips) | 0.2 |
| Same work through the on-box relay, 0.08 MP, 2 steps | 797 | 1.2 |
| + `--disable-dynamic-vram` (ComfyUI was re-staging 3.9 GB per prompt) | 678 | 1.4 |
| + img2img instead of reference-edit (half the image tokens) | 343 | 2.7 |
| + `--fast fp16_accumulation cublas_ops` | 283 | 3.3 |
| + 0.04 MP (256 px) | **224** | **4.1** |

End to end from the Mac over ZeroTier, driving the browser viewfinder: local layer **14–16 fps**,
diffusion **3.7–3.8 fps**, round trip **270–400 ms** (GPU 230–360 ms of it). The relay matters more than any
model setting: per-frame HTTP against ComfyUI cost about five round trips, and this link's round trip
ranged from 91 ms to 924 ms because ZeroTier relays traffic between these two machines rather than
connecting them directly.

So a live diffusion viewfinder runs at a few frames a second, not at camera rate. The viewfinder therefore
renders a local stand-in every frame and treats diffusion frames as refreshes.

**Distilling the model's colors.** Each diffusion frame also yields a LUT fitted from proxy→diffusion, which
the local layer then applies for free. It only helps when the model kept the scene, and at 256 px it often
does not: a 24×16 layout correlation between the camera frame and the diffusion frame measured

| Style | mean layout score | frames accepted at 0.7 |
|---|---:|---:|
| Golden Hour | 0.84 | all |
| Anime Painting | 0.66 | some |
| Toy Diorama | 0.63 | few |
| Travel Poster | 0.53 | rare |

Poster and diorama frequently wander into their own composition (a train close-up, a model village), so their
colors are rejected and the proxy is shown unchanged.

## Performance (measured)

| Operation | Where | Time |
|---|---|---:|
| Create a Look (grade + measurements) | Mac M3 | 0.4 s |
| Vision analysis (gemma3:4b, structured JSON) | Mac M3 | 20–45 s (background) |
| Tier 0 render, 1200×800 | Mac M3 | 0.15–0.35 s |
| Preview lookup, 1080p / 540p frame | Mac M3 | 28 ms / 8 ms |
| Brush paint (fast) | Mac M3 | 0.03 s |
| klein edit, 0.5 MP, warm | RTX 3060 Ti 8 GB | 2–11 s |
| klein edit, 1.0 MP | RTX 3060 Ti 8 GB | 21 s (VRAM spill) |
| Tier 1 render, end to end | box | 13.7 s |
| `/refine` (diffusion neutralizer) | box | 8.5 s |
| Brush paint (AI) | box | 10.8 s |
| klein GGUF edit, 0.25 MP cold / 0.5 MP | Mac M3 (MPS) | 71 s / 164 s |
| klein edit with Ollama gemma4 also on the GPU | box | >5 min (stalled) |
| Live preview local layer (proxy + distilled LUT) | Mac M3 | 17–66 ms/frame |
| Live preview diffusion frame, 256 px, end to end | box | 270–400 ms (3.8 fps) |
