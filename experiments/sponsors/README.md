# Sponsor MVPs (not part of the camera)

Small, standalone proofs for the tracks the write-up marks as "not built". They read the camera's own
data (`~/.nimbus/camera/photos/<id>/`) and talk to each sponsor's API directly. Nothing here is imported
by `device/` or `pipeline/`; each is one file with a `--dry-run` that shows exactly what it would send.

| Track | File | Needs |
|---|---|---|
| Dropbox — smarter photo library | `dropbox_library.py` | Keychain `dropbox-token` (a scoped app token: files.content.write, files.metadata.write) |
| Ramp — expense the Visa purchase | `ramp_expense.py` | Keychain `ramp-client-id`, `ramp-client-secret` (Ramp sandbox app) |
| Deepgram — speech to text | `deepgram_stt.py` | Keychain `deepgram-api-key` |

```bash
cd experiments/sponsors && uv run --with httpx python dropbox_library.py --dry-run
```
