# CST node graphs (D-Log M / HLG → Rec.709)

Resolve's scripting API **cannot create nodes or set CST parameters** (Blackmagic exposes `SetLUT`, `SetCDL`, `ApplyGradeFromDRX` only). The automated path is therefore:

1. Build each graph once by hand from `node-graphs.json` on a representative clip.
2. Color page → right-click viewer → **Grab Still** → Gallery → right-click still → **Export** → save as `DLogM_to_709.drx` / `HLG_to_709.drx` in this folder.
3. `resolve_assemble.py --drx-dlogm cst-node-graphs/DLogM_to_709.drx --drx-hlg cst-node-graphs/HLG_to_709.drx` applies the matching graph per clip (manifest `gamma` field). `gradeMode 0` = no keyframes.

`.drx` files are binary, version-specific Resolve exports. They are not in this repo because they have to come from your own Resolve install.

Quick previews outside Resolve: `ffkit.py proxy --to709` (HLG) or `--lut DJI_DLogM_to_Rec709.cube` (D-Log M). Those are approximations for editing only. The master grade always goes through these CST graphs.
