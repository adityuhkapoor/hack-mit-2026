# Sponsor tracks — the write-up, and what to show a judge

## Sponsor Track Write-Ups

**LongLake - Convince a non believer ✅**
The non-believer is the photographer who says AI ruins photos. Nimbus never touches the subject: the person or object is segmented (ISNet ∪ U²-Net human), only the surroundings are re-rendered, and the original pixels are composited back bit-for-bit with a 2 px edge blend. Every capture is then verified in 8-bit inside the eroded mask and the result is printed on the card: "subject unaltered · verified". The proof is measured, not asserted, and a test suite asserts it for every mode and every sensor extreme.

**Asus - Use Asus Hardware ✅**
The ASUS GX10 F470 (GB10, 128 GB unified memory) runs ComfyUI with FLUX.2 klein 9B (bf16 + fp4 8B text encoder) doing masked inpainting at 1.5 MP in ~19 s, the Nimbus API and Elasticsearch 8.15 — one box, no cloud image processing. klein 4B (~9 s) is the fast fallback profile. An A/B on the same photo: 4B kept the room and pasted in a stranger; 9B built the scene.

**Dropbox - Turn digital chaos into something useful, smarter photo libraries**
Every photo is understood, not just stored: Muse Spark (vision) writes caption, tags, scene, mood once per photo; the sensor readings are structured fields; the record is indexed for hybrid search. "Find the foggy ones from this morning", "what did I buy today", by voice. Then "save these to Dropbox": exactly that search result goes to a dated collection folder in the team's Dropbox app folder, each photo unchanged plus an index with captions, tags, times and readings (Dropbox API, OAuth app folder — [DROPBOX.md](DROPBOX.md)). Storage and search stay in Elasticsearch on the ASUS; Dropbox is where a collection is kept. Say so if asked, and do not claim the export is live until a team account has been linked and the smoke test run.

**Arduino - Use Uno Q and sensor data ✅**
The Uno Q's STM32 runs our sketch (compiled and flashed from the Uno Q's own Linux over ADB): it reads the MLX90640 thermal array and serves the frame to the Pi as an I2C peripheral, plus a button command reporting all four physical buttons with a pressed-since-last-read latch. Temperature → warmth, thermal frame change → motion blur, camera light → grain, mic → saturation; humidity/wind/cloud from Open-Meteo, labelled "(web)". Finding for Arduino: the Zephyr core's Wire cannot do a repeated start; the MLX90640 driver is patched to read through Zephyr's i2c_write_read.

**VISA - Redefine shopping experience with generative AI ✅**
Visa Buy mode: photograph a product; Muse names it exactly (brand, name, variant, size); a live search (Open Food Facts + web, Muse ranking into offers with merchant, price, link; estimates marked) finds it; "Buy with Visa" makes a real Visa Developer sandbox call — Visa Direct pull-funds with mutual TLS and Message Level Encryption (JWE RSA-OAEP-256/A128GCM) on Visa's test card — and shows Visa's approval code and transaction ID with a QR to the listing. Verified: Red Bull → Walmart → approved.

**Ramp - Adjacent to VISA**
Not built. The Visa receipt (merchant, amount, auth code, transaction ID, timestamp) is structured and could be posted to Ramp's expense API.

**OpenAI - A lot of GPT Astra was used ✅**
Used across development for design, code review and debugging. No OpenAI model runs in the product: reasoning and vision are Muse Spark, voice is ElevenLabs.

**Elastic - Used in the search of auto tagged photos (overlaps with Dropbox) ✅**
Elasticsearch 8.15: one document per capture (time, mode, readings, Muse tags, a bge-small dense_vector, URLs, product and receipt). Hybrid BM25 + kNN with range filters the voice agent extracts ("foggy" → min_rh 80, "this morning" → a time window). Measured air ranks ahead of what the picture looks like. SQLite + NumPy takes over if Elasticsearch is unreachable.

**The Token Company - Could be implemented in savings tokens for the image model**
Practice, not a feature: every Muse call is reasoning_effort="minimal" (~3× latency and token cut, no quality loss); tags, format choice and product lookups are computed once per photo and cached; prompts are compact templates; the plain-photo path renders on the camera with zero model tokens.

**Elevenlabs - Voice feedback from the camera itself and a novel way to interact with it ✅**
An ElevenLabs Conversational AI agent, push-to-talk from a physical button, Muse Spark as its custom LLM, ten client tools executing on the camera: take a photo, switch mode, read the air, search, show, details, send to phone, post to Instagram, identify product, buy it. First person as the camera; real values only. Lesson: Muse reasoning must be minimal or ElevenLabs drops the reply after each tool call.

**Deepgram - Maybe replace for Elevenlabs**
Not used; ElevenLabs handles STT and TTS inside its agent.

**Meta - Instagram Graph API ✅**
Muse Spark (muse-spark-1.3) runs the agent's reasoning and tool calls, tags every photo, chooses which of fifty formats a scene becomes and writes the headline in that format's voice, and identifies products. "Post it" publishes the card to @nimbus_hackmit2026 through the Instagram Graph API; "send it to my phone" shows a QR.

## Cheat sheet for the booth

One camera, one demo. Each track below names the exact thing to point at. Honest gaps are marked.

| Track | Status | Show this |
|---|---|---|
| **Arduino** — Uno Q + sensors | ✅ | The UNO Q reads the MLX90640 thermal array and the four buttons and serves both to the Pi over I2C (`device/unoq/nimbus_unoq.ino`). Temperature → hue, motion → blur, light → grain, sound → saturation, humidity → diffusion, all printed on the card. |
| **ASUS** — use ASUS hardware | ✅ | Every AI render runs on the ASUS GB10: FLUX.2 klein inpainting at 2 MP, Elasticsearch, and the Nimbus API. `curl 10.189.73.14:8000/health`. |
| **Long Lake** — convince a non-believer | ✅ pitch | The photographer's objection is "AI fakes the photo". Nimbus never touches the subject's pixels, checks it in 8-bit inside the mask, and prints "subject unaltered · verified" on every card. Show a Souvenir card, then the proof. |
| **Meta** — Muse Spark + Instagram | ✅ | Muse Spark is the camera's brain: it runs the voice agent's reasoning and tool calls, tags every photo (vision), names the Souvenir keepsake, and identifies products for Shop. "Post it" publishes to @nimbus_hackmit2026 through the Instagram Graph API. |
| **ElevenLabs** — voice | ✅ | Hold TALK and speak. An ElevenLabs Agent (Muse as its custom LLM) with ten client tools that run on the camera: take a photo, read the air, search, show, send to phone, post, identify, buy. The camera answers in its own voice. |
| **Elastic** — search | ✅ | Every photo is indexed in Elasticsearch 8.15 on the ASUS: hybrid BM25 + kNN (bge-small) with range filters on the readings. "Find the foggy ones from this morning" → `search_photos` builds the query. |
| **Visa** — reimagine shopping with generative AI | ✅ | **Discovery + checkout.** Photograph a thing, say "what is this?": Muse names it exactly (brand, variant, size), a live search (Open Food Facts + web) finds real listings — Target, Walmart, Instacart — with prices, and "buy it" pays with Visa — **a real call to the Visa Developer sandbox** (Visa Direct pull-funds with Message Level Encryption, Visa's test card): the receipt shows Visa's approval code and transaction id. Verified from the rig. (Without the sandbox credentials it falls back to a simulated approval and says so.) Prices not on the listing are marked `~` (estimated). Code: `device/nimbus_cam/shop.py`. |
| **OpenAI** | ✅ | Built with GPT-assisted development throughout (per the team's write-up). |
| **Dropbox** — digital chaos → useful; smarter photo libraries | ➕ partial | The library is searchable by meaning, weather and time, auto-tagged by Muse, and every photo carries its readings. A search result can be saved as a collection to the team Dropbox (app folder, Dropbox API) with a readable index — **only once the account is linked** ([DROPBOX.md](DROPBOX.md)); until then say the storage is Elasticsearch. The claim is the smarter library. |
| **The Token Company** — savings | ➕ write-up only | Nimbus renders on the camera with zero model tokens when the GPU is unreachable; tags and product lookups are computed once per photo and cached (`tags.json`, `shop.json`); every Muse call uses `reasoning_effort="minimal"`; prompts are compact. |

## The demo, in order

1. Hold a can of Red Bull. Press the shutter (D4). ~30 s: the surroundings become the measured air; the can
   and the hand are pixel-identical, "verified" on the card. *(Arduino, ASUS, Long Lake)*
2. MODE → Souvenir, shoot again: it becomes a trading card. *(Meta vision)*
3. Hold TALK: "what am I holding?" → "Red Bull Energy Drink, 250 ml — about $2.79 at Target. Buy it?"
   "Yes." → receipt on screen, Visa ····4242. *(Visa, ElevenLabs, Meta)*
4. "Find the foggy ones from this morning." *(Elastic)*  ·  "Post it." *(Meta / Instagram)*  ·  "Send it to my phone." (QR)
