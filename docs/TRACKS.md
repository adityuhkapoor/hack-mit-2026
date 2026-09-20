# Sponsor tracks — what to show a judge

One camera, one demo. Each track below names the exact thing to point at. Honest gaps are marked.

| Track | Status | Show this |
|---|---|---|
| **Arduino** — Uno Q + sensors | ✅ | The UNO Q reads the MLX90640 thermal array and the four buttons and serves both to the Pi over I2C (`device/unoq/nimbus_unoq.ino`). Temperature → hue, motion → blur, light → grain, sound → saturation, humidity → diffusion, all printed on the card. |
| **ASUS** — use ASUS hardware | ✅ | Every AI render runs on the ASUS GB10: FLUX.2 klein inpainting at 2 MP, Elasticsearch, and the Nimbus API. `curl 10.189.73.14:8000/health`. |
| **Long Lake** — convince a non-believer | ✅ pitch | The photographer's objection is "AI fakes the photo". Nimbus never touches the subject's pixels, checks it in 8-bit inside the mask, and prints "subject unaltered · verified" on every card. Show a Souvenir card, then the proof. |
| **Meta** — Muse Spark + Instagram | ✅ | Muse Spark is the camera's brain: it runs the voice agent's reasoning and tool calls, tags every photo (vision), names the Souvenir keepsake, and identifies products for Shop. "Post it" publishes to @arunningaround through the Instagram Graph API. |
| **ElevenLabs** — voice | ✅ | Hold TALK and speak. An ElevenLabs Agent (Muse as its custom LLM) with ten client tools that run on the camera: take a photo, read the air, search, show, send to phone, post, identify, buy. The camera answers in its own voice. |
| **Elastic** — search | ✅ | Every photo is indexed in Elasticsearch 8.15 on the ASUS: hybrid BM25 + kNN (bge-small) with range filters on the readings. "Find the foggy ones from this morning" → `search_photos` builds the query. |
| **Visa** — reimagine shopping with generative AI | ✅ (checkout simulated) | **Discovery + checkout.** Photograph a thing, say "what is this?": Muse names it exactly (brand, variant, size), a live search (Open Food Facts + web) finds real listings — Target, Walmart, Instacart — with prices, and "buy it" pays with Visa: a **simulated approval** by default (the receipt says so), or the **Visa Developer sandbox** (Visa Direct pull-funds, test card) when its credentials are in the Keychain (`visa-sandbox-user`, `visa-sandbox-password`, cert in `~/.nimbus/visa`). Prices not on the listing are marked `~` (estimated). Code: `device/nimbus_cam/shop.py`. |
| **OpenAI** | ✅ | Built with GPT-assisted development throughout (per the team's write-up). |
| **Dropbox** — digital chaos → useful; smarter photo libraries | ➕ partial | The library is searchable by meaning, weather and time, auto-tagged by Muse, and every photo carries its readings. **It does not use Dropbox's APIs** — say so if asked; the claim is the smarter library, not the storage. |
| **The Token Company** — savings | ➕ write-up only | Nimbus renders on the camera with zero model tokens when the GPU is unreachable; tags and product lookups are computed once per photo and cached (`tags.json`, `shop.json`); every Muse call uses `reasoning_effort="minimal"`; prompts are compact. |

## The demo, in order

1. Hold a can of Red Bull. Press the shutter (D4). ~30 s: the surroundings become the measured air; the can
   and the hand are pixel-identical, "verified" on the card. *(Arduino, ASUS, Long Lake)*
2. MODE → Souvenir, shoot again: it becomes a trading card. *(Meta vision)*
3. Hold TALK: "what am I holding?" → "Red Bull Energy Drink, 250 ml — about $2.79 at Target. Buy it?"
   "Yes." → receipt on screen, Visa ····4242. *(Visa, ElevenLabs, Meta)*
4. "Find the foggy ones from this morning." *(Elastic)*  ·  "Post it." *(Meta / Instagram)*  ·  "Send it to my phone." (QR)
