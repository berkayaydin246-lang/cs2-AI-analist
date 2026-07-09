# Replay Asset Pack

Drop custom replay visuals here. The replay engine loads these files automatically when present.

## Utility icons

Use transparent white SVG or PNG silhouettes. Keep the shape centered with padding.

- `icons/smoke-clean.png`
- `icons/flash.png`
- `icons/grenade-clean.png`
- `icons/molotov.png`

Recommended export: 128x128 or 256x256, transparent background, pure white foreground.

## Weapon icons

Player weapon icons are loaded from the `weapons` section in `manifest.json`.
Use transparent white PNGs, normalized to a wide canvas such as `512x256`, with
the barrel pointing right. Unknown or missing weapons fall back to the built-in
canvas glyph.

Currently wired:

- `weapons/ak47-clean.png`
- `weapons/awp-clean.png`
- `weapons/deagle-clean.png`
- `weapons/m4a1-clean.png`
- `weapons/m4a4-clean.png`
- `weapons/ssg-clean.png`
- `weapons/usp-clean.png`

## Effect textures

These are optional. Missing files are ignored. If an export contains a baked checkerboard background, create/use a cleaned transparent copy before wiring it in the manifest.

- `effects/grenadeeffect-clean.png`
- `effects/molotoveffect-clean.png`
- `effects/smokeeffect-clean.png`

For sprite sheets, use a horizontal strip of square frames, for example `4096x512` for 8 frames of `512x512`. Use transparent background and keep the effect centered in every frame.

After adding an effect file, update `manifest.json`, for example:

```json
"effects": {
  "heExplosion": {
    "src": "/static/assets/replay/effects/grenadeeffect-clean.png",
    "frames": 8,
    "alpha": 1,
    "scale": 1.18
  },
  "molotovFire": {
    "src": "/static/assets/replay/effects/molotoveffect-clean.png",
    "frames": 8,
    "alpha": 0.82,
    "scale": 1.05
  },
  "smokePuff": {
    "src": "/static/assets/replay/effects/smokeeffect-clean.png",
    "frames": 1,
    "alpha": 0.72,
    "scale": 1.15
  }
}
```
