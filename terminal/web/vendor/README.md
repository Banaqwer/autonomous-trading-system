# Vendored third-party files

Everything the page needs is served from this folder tree: no CDN at runtime, no build step.
Downloaded once on 2026-11-12 from jsDelivr (npm mirror). Verify with `sha256sum`.

| file | package · version | licence | bytes | sha256 |
|---|---|---|---:|---|
| `vendor/lightweight-charts.standalone.production.js` | lightweight-charts 4.1.3 (TradingView) | Apache-2.0 — `vendor/LICENSE-lightweight-charts.txt` | 160943 | `0c146b9bfbc6e5f19da1a0de876dae43023c332d5157d82f7575f9b7653ee519` |
| `vendor/fonts/jetbrains-mono-latin-400-normal.woff2` | @fontsource/jetbrains-mono 5.0.20 (JetBrains Mono Regular, latin subset) | SIL OFL 1.1 — `vendor/fonts/OFL.txt` | 21088 | `7c53386f55c866c1b4c9309c4bcf74eda10896aab3a1780b0af5cc4976e27a27` |
| `vendor/fonts/jetbrains-mono-latin-700-normal.woff2` | @fontsource/jetbrains-mono 5.0.20 (JetBrains Mono Bold, latin subset) | SIL OFL 1.1 — `vendor/fonts/OFL.txt` | 21956 | `ed8b8f71b22bf3b6e1ec1cf9b24dd999785aae33d6256950c1f86c97e423b6ef` |
| `data/land-110m.json` | world-atlas 2.0.2 (Natural Earth 1:110m land) | ISC (package) · public domain (Natural Earth data) — `data/LICENSE-world-atlas.txt` | 55207 | `ead5f68119c49a9250902e7da303bcb209341bbb8fefe7369a439b48b704658a` |
| `data/land-50m.json` | world-atlas 2.0.2 (Natural Earth 1:50m land) | same | 545534 | `619477ff690c086885e45cb91707d783805561bd75ae8e437b7d4694b0204e0f` |
| `data/countries-110m.json` | world-atlas 2.0.2 (Natural Earth 1:110m countries + land) | same | 107761 | `2516c915867c7baf18ddec727aec46c315541a07cfb3d79a6559b05d5e94eee8` |

Sources:

- https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js
  (global `LightweightCharts`; the licence requires the TradingView attribution link to stay visible on charts)
- https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.0.20/files/jetbrains-mono-latin-400-normal.woff2
- https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.0.20/files/jetbrains-mono-latin-700-normal.woff2
- https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json · land-50m.json · countries-110m.json

Notes:

- The font is the **latin subset**: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC,
  U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD.
  Glyphs outside it (for example ● ▲ ▼ → ✓) fall back to the next font of the stack and break the
  monospace grid: draw such marks with CSS or canvas, or use — · ‹ › ↑ ↓ … × ° ± which are included.
- The three world-atlas files were compared byte for byte (sha256) with the copies used in the
  audit prototype; all three match.
- Plotly is deliberately **not** vendored. The options slice decides about a lazy-loaded 3-D bundle.
- The world-atlas licence text in `data/LICENSE-world-atlas.txt` is the standard ISC text with the
  copyright line of the package (the npm tarball itself ships no licence file).
