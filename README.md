# R3ST: Toolkit

A Blender addon and RPG Maker MZ plugin pair for building FF7–FF9 style pre-rendered backgrounds.

Author backgrounds in Blender, export them with camera data baked in, and play them back in RPG Maker MZ with correct perspective — static ortho backgrounds with live perspective characters rendered on top, no drift, no manual alignment.

---

## What it does

**In Blender:**
- Camera rig with yaw / pitch / distance / FOV controls matching RPG Maker's mz3d camera system
- Geometry tagging system — mark meshes as background, foreground, or character layer, assign tileset sheet and tile coordinates
- Tileset sheet generator — builds RPG Maker-compatible PNG sheets directly from Blender
- Two-pass preview render — composites ortho background + perspective characters into a live preview inside Blender
- Continuous Render mode — auto-updates the preview whenever the scene changes
- Walk Mode — move a `_character` object through the scene in real time with WASD, camera tracks live; use with Continuous Render to catch scale and perspective problems before committing to a bake
- One-click export — writes geometry, camera parameters, and map JSON ready for RPG Maker

**In RPG Maker MZ:**
- Hooks into mz3d (by Cutievirus) via OrthoGroups
- Background and foreground geometry rendered in ORTHO (no perspective drift)
- Characters rendered in PERSP within the same frame
- Camera parameters read directly from map JSON — no manual tuning

---

## Requirements

- Blender 5.0
- RPG Maker MZ
- [mz3d](https://cutievirus.itch.io/mz3d) by Cutievirus (purchased separately)
- MZ3D_OrthoGroups.js (bundled)

Plugin load order: `mz3d.js` → `MZ3D_RenderGroup.js` → `MZ3D_OrthoGroups.js` → `r3st_toolkit.js`

---

## Installation

1. Download `r3st_toolkit.py` and `r3st_toolkit.js`
2. In Blender: Edit → Preferences → Add-ons → Install → select `r3st_toolkit.py` → enable **R3ST: Toolkit**
3. In RPG Maker MZ: copy `r3st_toolkit.js` into your project's `js/plugins/` folder and add it via the Plugin Manager

The addon lives in the **N panel → R3ST tab** inside any 3D viewport.

---

## Scale convention

1 Blender unit = 1 RPG Maker tile = 48px

Rooms spawn with the bottom-left corner at world origin (0, 0, 0).

---

## Status

Work in progress. Not yet public. itch.io page in progress.
