# =============================================================================
# r3st_toolkit.py  —  Blender Add-on
# =============================================================================
# RMMZ 3D Scene Toolkit (R3ST)
# Blender tools for building pre-rendered backgrounds for RPG Maker MZ.
#
# Requires the mz3d plugin for RPG Maker MZ (purchased separately).
#
# Install:
#   Option A — copy this file to:
#     %APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\
#   Option B — Edit > Preferences > Add-ons > Install… → select this file
#   Then enable "Game Engine: RMMZ 3D Scene Toolkit" in the list.
#
# Usage:
#   3D Viewport → N panel → R3ST tab
# =============================================================================

bl_info = {
    "name":        "RMMZ 3D Scene Toolkit",
    "description": "Blender tools for building pre-rendered backgrounds for RPG Maker MZ. Requires the mz3d plugin (purchased separately).",
    "author":      "Claude.ai and Dimanology",
    "version":     (1, 3, 0),
    "blender":     (4, 0, 0),
    "location":    "View3D > Sidebar > R3ST",
    "category":    "Game Engine",
}

import bpy
import bmesh
import json
import math
import os
import re
import struct
import zlib
from bpy.props import (
    IntProperty, FloatProperty, FloatVectorProperty,
    EnumProperty, StringProperty, BoolProperty,
)
from bpy.types import PropertyGroup, Operator, Panel

PI = math.pi

# ── Object / collection names ─────────────────────────────────────────────────
COLLECTION_NAME = 'Fixed Camera Angle'
PIVOT_NAME      = 'R3ST_Pivot'
ARM_NAME        = 'R3ST_CameraArm'
CAM_NAME        = 'R3ST_Camera'
CAM_ORTHO_NAME  = 'R3ST_CamOrtho'
ROOM_NAME       = 'R3ST_Room'
CUBE_NAME       = 'R3ST_UnitCube'
SENSOR_H        = 18.0   # mm — virtual sensor height

# Preview scene names
SCENE_MAIN  = 'R3ST_Main'
SCENE_ORTHO = 'R3ST_Ortho'
SCENE_PERSP = 'R3ST_Persp'

_COL_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

# Hardcoded room / tile image colors (not exposed in UI — edit here if needed)
_ROOM_COLOR_A      = (0.90, 0.90, 0.88, 1.0)   # off-white tile
_ROOM_COLOR_B      = (0.55, 0.55, 0.55, 1.0)   # grey tile
_ROOM_TEXT_COLOR   = (0.05, 0.05, 0.05, 1.0)   # dark label text
_ROOM_BORDER_COLOR = (0.20, 0.20, 0.20, 1.0)   # tile border
_ROOM_CUBE_COLOR   = (0.80, 0.35, 0.05, 1.0)   # unit-cube orange

# Hardcoded rig spawn defaults
_RIG_YAW   =  0.0
_RIG_PITCH = 45.0
_RIG_DIST  =  9.0
_RIG_FOV   = 45.0
_RIG_ROLL  =  0.0

# Hardcoded tileset sheet colors (not exposed in UI — edit here if needed)
_TS_ACTIVE_COLOR   = (0.18, 0.38, 0.50, 1.0)
_TS_INACTIVE_COLOR = (0.08, 0.08, 0.08, 1.0)
_TS_RESERVED_COLOR = (0.38, 0.06, 0.06, 1.0)
_TS_TEXT_COLOR     = (1.00, 1.00, 1.00, 1.0)
_TS_BORDER_COLOR   = (0.00, 0.00, 0.00, 1.0)

# Tileset sheet definitions (A1–A4 skipped — autotile format)
# 'reserved': True means (col=0, row=0) is the RPG Maker erase slot
_SHEET_DEFS = {
    'A5': {'w': 384, 'h': 768,  'cols': 8,  'rows': 16, 'reserved': False},
    'B':  {'w': 768, 'h': 768,  'cols': 16, 'rows': 16, 'reserved': True},
    'C':  {'w': 768, 'h': 768,  'cols': 16, 'rows': 16, 'reserved': False},
    'D':  {'w': 768, 'h': 768,  'cols': 16, 'rows': 16, 'reserved': False},
    'E':  {'w': 768, 'h': 768,  'cols': 16, 'rows': 16, 'reserved': False},
}


# ══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def remove_obj(name):
    obj = bpy.data.objects.get(name)
    if obj:
        bpy.data.objects.remove(obj, do_unlink=True)


def remove_mat(name):
    mat = bpy.data.materials.get(name)
    if mat:
        bpy.data.materials.remove(mat)


def get_or_create_collection(name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    elif col.name not in [c.name for c in bpy.context.scene.collection.children]:
        bpy.context.scene.collection.children.link(col)
    return col


def link_to_collection(obj, col):
    for c in obj.users_collection:
        c.objects.unlink(obj)
    col.objects.link(obj)


def add_custom_prop(obj, name, value, mn, mx, desc):
    obj[name] = value
    obj.id_properties_ui(name).update(min=mn, max=mx, description=desc, default=value)


def add_driver(target, data_path, index, source, var_specs, expression):
    fc = target.driver_add(data_path, index) if index >= 0 \
         else target.driver_add(data_path)
    d = fc.driver
    d.type = 'SCRIPTED'
    while d.variables:
        d.variables.remove(d.variables[0])
    for vname, vpath in var_specs:
        v = d.variables.new()
        v.name = vname
        v.type = 'SINGLE_PROP'
        v.targets[0].id = source
        v.targets[0].data_path = vpath
    d.expression = expression


def col_letter(col_idx):
    """0 → 'A', 1 → 'B', … 25 → 'Z', 26 → 'AA', etc."""
    s = ''
    n = col_idx
    while True:
        s = _COL_LETTERS[n % 26] + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def tile_image_name(col_idx, row_idx):
    return f'{col_letter(col_idx)}{row_idx}'


# ══════════════════════════════════════════════════════════════════════════════
# PIXEL FONT  (3×5 bitmap, digits 0–9 + letters A–Z)
# ══════════════════════════════════════════════════════════════════════════════

_FONT = {
    '0': [0b111,0b101,0b101,0b101,0b111], '1': [0b010,0b110,0b010,0b010,0b111],
    '2': [0b111,0b001,0b111,0b100,0b111], '3': [0b111,0b001,0b011,0b001,0b111],
    '4': [0b101,0b101,0b111,0b001,0b001], '5': [0b111,0b100,0b111,0b001,0b111],
    '6': [0b111,0b100,0b111,0b101,0b111], '7': [0b111,0b001,0b010,0b010,0b010],
    '8': [0b111,0b101,0b111,0b101,0b111], '9': [0b111,0b101,0b111,0b001,0b111],
    'A': [0b010,0b101,0b111,0b101,0b101], 'B': [0b110,0b101,0b110,0b101,0b110],
    'C': [0b011,0b100,0b100,0b100,0b011], 'D': [0b110,0b101,0b101,0b101,0b110],
    'E': [0b111,0b100,0b111,0b100,0b111], 'F': [0b111,0b100,0b111,0b100,0b100],
    'G': [0b011,0b100,0b101,0b101,0b011], 'H': [0b101,0b101,0b111,0b101,0b101],
    'I': [0b111,0b010,0b010,0b010,0b111], 'J': [0b001,0b001,0b001,0b101,0b010],
    'K': [0b101,0b101,0b110,0b101,0b101], 'L': [0b100,0b100,0b100,0b100,0b111],
    'M': [0b101,0b111,0b101,0b101,0b101], 'N': [0b101,0b111,0b111,0b101,0b101],
    'O': [0b010,0b101,0b101,0b101,0b010], 'P': [0b110,0b101,0b110,0b100,0b100],
    'Q': [0b010,0b101,0b101,0b110,0b011], 'R': [0b110,0b101,0b110,0b101,0b101],
    'S': [0b011,0b100,0b010,0b001,0b110], 'T': [0b111,0b010,0b010,0b010,0b010],
    'U': [0b101,0b101,0b101,0b101,0b111], 'V': [0b101,0b101,0b101,0b101,0b010],
    'W': [0b101,0b101,0b101,0b111,0b101], 'X': [0b101,0b101,0b010,0b101,0b101],
    'Y': [0b101,0b101,0b010,0b010,0b010], 'Z': [0b111,0b001,0b010,0b100,0b111],
    ',': [0b000,0b000,0b010,0b010,0b100],  # comma — tail curves left-down
}


def _str_px_width(s, scale):
    if not s: return 0
    return len(s) * (3 * scale) + (len(s) - 1) * scale


def _draw_string(pixels, iw, ih, text, cx, cy, scale, color):
    r, g, b, a = color
    cw = 3 * scale
    for ch in text:
        rows = _FONT.get(ch)
        if rows is None:
            cx += cw + scale; continue
        for ri, bits in enumerate(reversed(rows)):
            for bi in range(3):
                if bits & (0b100 >> bi):
                    for dy in range(scale):
                        for dx in range(scale):
                            x = cx + bi * scale + dx
                            y = cy + ri * scale + dy
                            if 0 <= x < iw and 0 <= y < ih:
                                idx = (y * iw + x) * 4
                                pixels[idx]=r; pixels[idx+1]=g
                                pixels[idx+2]=b; pixels[idx+3]=a
        cx += cw + scale


# ══════════════════════════════════════════════════════════════════════════════
# PNG WRITER  (pure Python — no PIL required)
# ══════════════════════════════════════════════════════════════════════════════

def _write_png(path, pixels_rgba_float, w, h):
    rows = []
    for row in range(h - 1, -1, -1):   # bottom-up → top-down
        rb = bytearray()
        for col in range(w):
            i = (row * w + col) * 4
            rb += bytes([int(pixels_rgba_float[i]*255),
                         int(pixels_rgba_float[i+1]*255),
                         int(pixels_rgba_float[i+2]*255)])
        rows.append(rb)

    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', c)

    raw = b''.join(b'\x00' + bytes(r) for r in rows)
    png  = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 6))
    png += chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(png)


def _make_tile_pixels(col, row, tile_px, color_a, color_b, text_color, border_color):
    """Return flat RGBA float pixel list for one tile (bottom-up, y=0 is bottom)."""
    bg    = color_a if (col + row) % 2 == 0 else color_b
    scale = max(2, tile_px // 16)
    pixels = list(bg) * (tile_px * tile_px)
    bc = list(border_color)

    # Border
    for x in range(tile_px):
        for y in [0, tile_px - 1]:
            i = (y * tile_px + x) * 4; pixels[i:i+4] = bc
    for y in range(tile_px):
        for x in [0, tile_px - 1]:
            i = (y * tile_px + x) * 4; pixels[i:i+4] = bc

    # Centred label
    label = tile_image_name(col, row)
    tw = _str_px_width(label, scale)
    th = 5 * scale
    tx = (tile_px - tw) // 2
    ty = (tile_px - th) // 2
    _draw_string(pixels, tile_px, tile_px, label, tx, ty, scale, text_color)
    return pixels


def _make_ts_tile_pixels(col, row, tile_px, kind, sheet,
                         active_color, inactive_color, reserved_color,
                         text_color, border_color):
    """
    Generate pixel data for one tileset sheet tile.
      kind  : 'active'   — colored + sheet letter (top) + col,row (bottom)
              'inactive' — dark + diagonal X
              'reserved' — red-tinted + sheet letter (top) + 0,0 (bottom)
      sheet : e.g. 'A5', 'B', 'C', 'D', 'E'
    col/row are mz3d coordinates (row=0 = top of sheet).
    """
    if   kind == 'active':   bg = list(active_color)
    elif kind == 'reserved': bg = list(reserved_color)
    else:                    bg = list(inactive_color)

    pixels = bg * (tile_px * tile_px)
    bc     = list(border_color)

    # Border
    for x in range(tile_px):
        for y in [0, tile_px - 1]:
            i = (y * tile_px + x) * 4; pixels[i:i+4] = bc
    for y in range(tile_px):
        for x in [0, tile_px - 1]:
            i = (y * tile_px + x) * 4; pixels[i:i+4] = bc

    if kind == 'inactive':
        # Diagonal X lines
        xc = list(border_color)
        for i in range(1, tile_px - 1):
            idx = (i * tile_px + i) * 4;              pixels[idx:idx+4] = xc
            idx = (i * tile_px + (tile_px-1-i)) * 4;  pixels[idx:idx+4] = xc
    else:
        MARGIN = 3   # px gap from the border on each side

        # ── Top half: sheet letter as large as possible ───────────────────────
        s_let = min(
            (tile_px - 2 * MARGIN) // (3 * len(sheet) + max(0, len(sheet) - 1)),
            (tile_px // 2 - 2 * MARGIN) // 5,
        )
        s_let = max(1, s_let)
        lw = _str_px_width(sheet, s_let)
        lx = (tile_px - lw) // 2
        # y=0 is bottom of buffer; top-half in display = upper y values
        ly = tile_px - MARGIN - 5 * s_let
        _draw_string(pixels, tile_px, tile_px, sheet, lx, ly, s_let, tuple(text_color))

        # ── Bottom half: col,row as large as possible ─────────────────────────
        coord = f'{col},{row}'
        # worst-case width formula: len(coord) glyphs × 4 - 1 columns at scale 1
        s_num = min(
            (tile_px - 2 * MARGIN) // max(1, 4 * len(coord) - 1),
            (tile_px // 2 - 2 * MARGIN) // 5,
        )
        s_num = max(1, s_num)
        nw = _str_px_width(coord, s_num)
        nx = (tile_px - nw) // 2
        ny = MARGIN   # pushed to bottom edge of tile
        _draw_string(pixels, tile_px, tile_px, coord, nx, ny, s_num, tuple(text_color))

    return pixels


def generate_tileset_sheets(out_dir, package_name, active_count, tile_px):
    """
    Write R3ST_{name}_A5.png, R3ST_{name}_B.png, ... into out_dir.
    Active tiles fill left→right, top→bottom, skipping the reserved (0,0) slot on B–E.
    row=0 = TOP of the sheet image (matching RPG Maker / mz3d convention).
    Colors are hardcoded via _TS_* constants at the top of the file.
    """
    os.makedirs(out_dir, exist_ok=True)
    pkg             = package_name
    active_color    = _TS_ACTIVE_COLOR
    inactive_color  = _TS_INACTIVE_COLOR
    reserved_color  = _TS_RESERVED_COLOR
    text_color      = _TS_TEXT_COLOR
    border_color    = _TS_BORDER_COLOR
    written = []

    for sheet, defs in _SHEET_DEFS.items():
        cols          = defs['cols']
        rows          = defs['rows']
        w             = defs['w']
        h             = defs['h']
        has_reserved  = defs['reserved']

        # Collect first active_count usable positions (row=0 is TOP, reading order)
        active_set = set()
        count = 0
        for r in range(rows):
            for c in range(cols):
                if has_reserved and c == 0 and r == 0:
                    continue          # reserved slot
                active_set.add((c, r))
                count += 1
                if count >= active_count:
                    break
            if count >= active_count:
                break

        # Full sheet pixel buffer (bottom-up internally; flipped by _write_png)
        pixels = [0.0] * (w * h * 4)

        for r in range(rows):
            for c in range(cols):
                if has_reserved and c == 0 and r == 0:
                    kind = 'reserved'
                elif (c, r) in active_set:
                    kind = 'active'
                else:
                    kind = 'inactive'

                tile_px_data = _make_ts_tile_pixels(
                    c, r, tile_px, kind, sheet,
                    active_color, inactive_color, reserved_color,
                    text_color, border_color)

                # row=0 (top in mz3d) → highest y in our bottom-up buffer
                # so it ends up at the top of the PNG after the flip in _write_png
                ox = c * tile_px
                oy = (rows - 1 - r) * tile_px

                for ty in range(tile_px):
                    src = (ty * tile_px) * 4
                    dst = ((oy + ty) * w + ox) * 4
                    pixels[dst : dst + tile_px * 4] = tile_px_data[src : src + tile_px * 4]

        fname = f'R3ST_{pkg}_{sheet}.png'
        fpath = os.path.join(out_dir, fname)
        _write_png(fpath, pixels, w, h)
        written.append(fpath)
        print(f'[R3ST] Tileset sheet → {fname}')

    return written


def generate_tile_images(out_dir, room_w, room_d, tile_px,
                         color_a, color_b, text_color, border_color):
    """Write one PNG per floor tile. Returns list of (col, row, filepath)."""
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for col in range(room_w):
        for row in range(room_d):
            pixels = _make_tile_pixels(col, row, tile_px,
                                       color_a, color_b, text_color, border_color)
            fpath = os.path.join(out_dir, tile_image_name(col, row) + '.png')
            _write_png(fpath, pixels, tile_px, tile_px)
            results.append((col, row, fpath))
    return results


def bake_map_image(out_dir, room_w, room_d, tile_px,
                   color_a, color_b, text_color, border_color,
                   save_path=None):
    """
    Composite all tile images into one large PNG.
    Output size: (room_w * tile_px) × (room_d * tile_px).
    Reads tiles from out_dir (_R3ST).
    Saves to save_path if given, otherwise 'grid_map.png' in out_dir.
    col=0 is left (west), row=0 is bottom (south) — matches Blender world.
    """
    iw = room_w * tile_px
    ih = room_d * tile_px
    big = [0.0] * (iw * ih * 4)

    for col in range(room_w):
        for row in range(room_d):
            fpath = os.path.join(out_dir, tile_image_name(col, row) + '.png')

            if os.path.exists(fpath):
                # Load via Blender so we don't need a PNG reader
                tmp = bpy.data.images.load(fpath, check_existing=False)
                tile_pixels = list(tmp.pixels)
                bpy.data.images.remove(tmp)
            else:
                # Re-generate on the fly if file is missing
                tile_pixels = _make_tile_pixels(col, row, tile_px,
                                                color_a, color_b, text_color, border_color)

            # Paste tile into big buffer
            # row=0 → bottom of image → oy=0 in bottom-up pixel space
            ox = col * tile_px
            oy = row * tile_px
            for y in range(tile_px):
                src_base = (y * tile_px) * 4
                dst_base = ((oy + y) * iw + ox) * 4
                big[dst_base : dst_base + tile_px * 4] = \
                    tile_pixels[src_base : src_base + tile_px * 4]

    out_path = save_path if save_path else os.path.join(out_dir, 'grid_map.png')
    _write_png(out_path, big, iw, ih)
    return out_path


# ══════════════════════════════════════════════════════════════════════════════
# MATERIAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _mat_from_image_path(mat_name, img_path):
    remove_mat(mat_name)
    img = bpy.data.images.get(os.path.basename(img_path))
    if img is None:
        img = bpy.data.images.load(img_path, check_existing=True)

    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    N = mat.node_tree.nodes; L = mat.node_tree.links; N.clear()

    out  = N.new('ShaderNodeOutputMaterial'); out.location  = ( 400, 0)
    emit = N.new('ShaderNodeEmission');       emit.location = ( 100, 0)
    tex  = N.new('ShaderNodeTexImage');       tex.location  = (-200, 0)
    uv   = N.new('ShaderNodeUVMap');          uv.location   = (-450, 0)

    uv.uv_map = 'UVMap'; tex.image = img; tex.extension = 'CLIP'
    emit.inputs['Strength'].default_value = 1.0

    L.new(uv.outputs['UV'],         tex.inputs['Vector'])
    L.new(tex.outputs['Color'],     emit.inputs['Color'])
    L.new(emit.outputs['Emission'], out.inputs['Surface'])
    return mat


def _solid_emit_mat(mat_name, color):
    remove_mat(mat_name)
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    N = mat.node_tree.nodes; L = mat.node_tree.links; N.clear()

    out  = N.new('ShaderNodeOutputMaterial'); out.location = (300, 0)
    emit = N.new('ShaderNodeEmission');       emit.location = (0, 0)
    emit.inputs['Color'].default_value    = color
    emit.inputs['Strength'].default_value = 1.0
    L.new(emit.outputs['Emission'], out.inputs['Surface'])
    mat.diffuse_color = color
    return mat


# ══════════════════════════════════════════════════════════════════════════════
# RPG MAKER MAP HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_map_infos(data_dir):
    """Read MapInfos.json or return a minimal [null] list if it doesn't exist."""
    path = os.path.join(data_dir, 'MapInfos.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return [None]


def _save_map_infos(data_dir, infos):
    path = os.path.join(data_dir, 'MapInfos.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(infos, f, ensure_ascii=False)


def _find_map_by_name(infos, name):
    """Return (index, entry) for the first entry whose 'name' matches, else (None, None)."""
    for i, entry in enumerate(infos):
        if entry and entry.get('name') == name:
            return i, entry
    return None, None


def _next_map_id(infos):
    return max((e['id'] for e in infos if e and isinstance(e.get('id'), int)), default=0) + 1


def _next_map_order(infos):
    return max((e['order'] for e in infos if e and isinstance(e.get('order'), int)), default=0) + 1


# ── Tilesets.json helpers ─────────────────────────────────────────────────────

def _load_tilesets(data_dir):
    path = os.path.join(data_dir, 'Tilesets.json')
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def _save_tilesets(data_dir, tilesets):
    path = os.path.join(data_dir, 'Tilesets.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(tilesets, fh, ensure_ascii=False)


def _find_tileset_by_name(tilesets, name):
    """Return (index, entry) for the first tileset whose name matches, else (None, None)."""
    for i, entry in enumerate(tilesets):
        if entry and entry.get('name') == name:
            return i, entry
    return None, None


def _next_tileset_id(tilesets):
    return max((e['id'] for e in tilesets if e and isinstance(e.get('id'), int)), default=0) + 1


def _make_blank_tileset_entry(tileset_id, name, tileset_names):
    """Return a minimal valid RPG Maker MZ Tilesets.json entry."""
    return {
        "flags":        [0] * 8192,
        "id":           tileset_id,
        "mode":         1,
        "name":         name,
        "note":         "",
        "tilesetNames": tileset_names,
    }


def _build_tileset_names(ts_dir, map_name):
    """
    Return the 9-slot tilesetNames array for an R3ST package.
    Slots 0-3 (A1-A4) are always ''.
    Slots 4-8 (A5, B, C, D, E) are filled only if the PNG exists on disk.
    Values are filenames without extension, matching RPG Maker convention.
    """
    _SLOT = {'A5': 4, 'B': 5, 'C': 6, 'D': 7, 'E': 8}
    names = ['', '', '', '', '', '', '', '', '']
    for sheet, slot in _SLOT.items():
        fname = f'R3ST_{map_name}_{sheet}.png'
        if os.path.exists(os.path.join(ts_dir, fname)):
            names[slot] = f'R3ST_{map_name}_{sheet}'
    return names


def _make_blank_map(width, height, note=''):
    """Return a minimal valid RPG Maker MZ map dict."""
    data = [0] * (width * height * 6)   # 6 layers, all empty
    return {
        "autoplayBgm": False, "autoplayBgs": False,
        "battleback1Name": "", "battleback2Name": "",
        "bgm": {"name":"","pan":0,"pitch":100,"volume":90},
        "bgs": {"name":"","pan":0,"pitch":100,"volume":90},
        "disableDashing": False, "displayName": "",
        "encounterList": [], "encounterStep": 30,
        "height": height, "note": note,
        "parallaxLoopX": False, "parallaxLoopY": False,
        "parallaxName": "", "parallaxShow": False,
        "parallaxSx": 0, "parallaxSy": 0,
        "scrollType": 0, "specifyBattleback": False,
        "tilesetId": 1, "width": width,
        "data": data, "events": [None],
    }


def _build_mz3d_note(map_name):
    """
    Scan all scene mesh objects tagged with r3st_map == map_name.
    Objects sharing the same r3st_export_group are collapsed into one entry
    using {group}.{ext} as the model filename.
    Ungrouped objects each get their own entry using {obj.name}.{ext}.
    Returns a <mz3d-tiles> notetag string, or '' if nothing is tagged.
    """
    # grouped: (group, ext) → (sheet, col, row, rg)  — first object wins
    grouped   = {}
    # ungrouped: list of individual entry strings
    ungrouped = []

    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if obj.get('r3st_map') != map_name:
            continue
        sheet = obj.get('r3st_sheet', 'B')
        col   = int(obj.get('r3st_col', 1))
        row   = int(obj.get('r3st_row', 0))
        rg    = int(obj.get('r3st_render_group', 1))
        grp   = obj.get('r3st_export_group', '').strip()
        # R3ST_Room is always .obj — never let a stray export_type override that
        if obj.name == ROOM_NAME:
            ext = 'obj'
        else:
            ext = obj.get('r3st_export_type', 'OBJ').lower()

        if grp:
            key = (grp, ext)
            if key not in grouped:
                grouped[key] = (sheet, col, row, rg)
        else:
            model = f'{obj.name}.{ext}'
            ungrouped.append(
                f'{sheet},{col},{row}:model({model}),renderGroup({rg}),climb(false)')

    entries = []
    for (grp, ext), (sheet, col, row, rg) in grouped.items():
        model = f'{grp}.{ext}'
        entries.append(
            f'{sheet},{col},{row}:model({model}),renderGroup({rg}),climb(false)')
    entries.extend(ungrouped)

    if not entries:
        return ''
    return '<mz3d-tiles>\n' + '\n'.join(entries) + '\n</mz3d-tiles>'


# ── Package scanner (for geometry tag dropdown) ───────────────────────────────
# Module-level list prevents Blender's EnumProperty from garbage-collecting items
_pkg_cache = [('NONE', '— no packages found —', '')]


def _scan_packages(project_dir):
    """Return sorted list of R3ST package names found in img/tilesets/."""
    if not project_dir:
        return []
    root   = bpy.path.abspath(project_dir)
    ts_dir = os.path.join(root, 'img', 'tilesets')
    if not os.path.isdir(ts_dir):
        return []
    pkgs = set()
    try:
        for f in os.listdir(ts_dir):
            if f.upper().startswith('R3ST_') and f.lower().endswith('.png'):
                stem  = f[5:-4]                          # strip 'R3ST_' and '.png'
                parts = stem.rsplit('_', 1)              # split off sheet suffix
                if len(parts) == 2 and parts[1] in ('A5', 'B', 'C', 'D', 'E'):
                    pkgs.add(parts[0])
    except Exception:
        pass
    return sorted(pkgs)


def _refresh_pkg_cache(project_dir):
    global _pkg_cache
    pkgs = _scan_packages(project_dir)
    _pkg_cache = [(p, p, '') for p in pkgs] if pkgs else [('NONE', '— no R3ST packages found —', '')]


def _get_pkg_items(self, context):
    if context is not None:
        s = getattr(context.scene, 'r3st_setup', None)
        if s is not None:
            _refresh_pkg_cache(s.project_dir)
    return _pkg_cache


# ══════════════════════════════════════════════════════════════════════════════
# PROPERTY GROUPS
# ══════════════════════════════════════════════════════════════════════════════

class R3ST_SetupProps(PropertyGroup):
    """Global project settings — set once, used everywhere."""
    project_dir: StringProperty(
        name="Project Root", default="", subtype='DIR_PATH',
        description="Root folder of your RPG Maker MZ project (contains game.rmmzproject)")
    tile_px: IntProperty(
        name="Tile px", default=48, min=16, max=128,
        description="Pixel size per tile — must match RPG Maker Options › Tile Size (default 48)")
    res_x: IntProperty(name="Res W", default=1920, min=1,
                       description="Render / parallax width in pixels")
    res_y: IntProperty(name="Res H", default=1080, min=1,
                       description="Render / parallax height in pixels")
    persp_collection: StringProperty(
        name="Persp Collection",
        default="Characters",
        description="Collection whose objects render in PERSPECTIVE (e.g. Characters). "
                    "All other collections render in ORTHOGRAPHIC (no parallax). "
                    "Used by the Preview Scenes setup."
    )
    preview_pct: EnumProperty(
        name="Preview Quality",
        description="Resolution percentage for continuous / preview renders. "
                    "Lower = faster update, higher = sharper image.",
        items=[
            ('100', "Full",    "Render at full resolution (slowest)",  'RESTRICT_RENDER_OFF', 0),
            ('50',  "Half",    "Render at 50 % — 4× fewer pixels",    'ANTIALIASED',         1),
            ('25',  "Quarter", "Render at 25 % — 16× fewer pixels",   'ALIASED',             2),
        ],
        default='100',
    )
    active_tab: EnumProperty(
        name="Tab",
        items=[
            ('SETUP',     '', "Scene Setup",        'SCENE_DATA',      0),
            ('TILESETS',  '', "Tilesets Generator", 'IMAGE_DATA',      1),
            ('GEO_TAG',   '', "Geometry Tag",       'OBJECT_DATAMODE', 2),
            ('GENERATOR', '', "Scene Generator",    'GRID',            3),
            ('EXPORT',    '', "Export",             'EXPORT',          4),
            ('PREVIEW',   '', "Preview",            'RENDER_STILL',    5),
        ],
        default='SETUP',
    )


class R3ST_RoomProps(PropertyGroup):
    room_w: IntProperty(name="W", default=10, min=1, max=256,
                        description="Room width  in tiles (east, Blender +X)")
    room_d: IntProperty(name="D", default=10, min=1, max=256,
                        description="Room depth  in tiles (north, Blender +Y)")
    room_h: IntProperty(name="H", default=1,  min=1, max=50,
                        description="Room height in tiles (up, Blender +Z)")


class R3ST_TilesetProps(PropertyGroup):
    # ── Generator settings ────────────────────────────────────────────────────
    map_name:     StringProperty(
        name="Map Name", default="Map_01",
        description="Name for tileset PNGs and the RPG Maker map entry "
                    "(spaces allowed — R3ST_{name}_B.png)")
    active_count: IntProperty(
        name="Active Tiles", default=10, min=1, max=128,
        description="Number of labeled geometry-binding tiles per sheet (reading order, skips reserved)")

    # ── Geometry tag ──────────────────────────────────────────────────────────
    tag_map:   EnumProperty(
        name="Target Map", items=_get_pkg_items,
        description="Which R3ST map to bind this geometry to (scanned from img/tilesets/)")
    tag_sheet: EnumProperty(
        name="Sheet",
        items=[('A5','A5',''),('B','B',''),('C','C',''),('D','D',''),('E','E','')],
        default='B',
        description="Tileset sheet")
    tag_col:   IntProperty(name="Col", default=1, min=0, max=15,
                           description="Tile column (0 = reserved on B–E)")
    tag_row:   IntProperty(name="Row", default=0, min=0, max=15,
                           description="Tile row (0 = top of sheet)")
    tag_render_group: EnumProperty(
        name="Render Group",
        description="Babylon.js renderingGroupId — controls draw order and OrthoGroups projection mode",
        items=[
            ('0', "0  [ORTHO]  Background",  "Ortho — renders below everything (skybox layer)"),
            ('1', "1  [ORTHO]  Default",     "Ortho — standard layer: tiles, walls, room geometry"),
            ('2', "2  [PERSP]  Characters",  "Persp — perspective layer: player and NPCs (mz3d default)"),
            ('3', "3  [ORTHO]  Foreground",  "Ortho — always on top: foreground overlays, doodads in front of characters"),
        ],
        default='1',
    )
    tag_export_type: EnumProperty(
        name="Export Type",
        description="OBJ = collision mesh (moff physics, characters blocked)  |  GLB = visual doodad (characters pass through, bound to tileset)",
        items=[
            ('OBJ', "OBJ", "Collision mesh — exported as .obj, readable by moff physics"),
            ('GLB', "GLB", "Visual doodad — exported as .glb, attached to tileset via mz3d-tiles"),
        ],
        default='GLB',
    )
    tag_export_group: StringProperty(
        name="Export Group",
        default="",
        description="Objects sharing the same group name are bundled into one exported file  (e.g. 'fg_pillars' → fg_pillars.glb)"
    )


class R3ST_ExportProps(PropertyGroup):
    mz3d_default_fov: FloatProperty(name="Plugin FOV", default=70.0, min=5.0, max=120.0,
                                    description="mz3d Plugin Manager 'fov' — used to calculate zoom")
    camera_mode: EnumProperty(name="Camera Mode",
                              items=[('p',"Perspective",""),('o',"Orthographic","")],
                              default='p')
    tile_layer: EnumProperty(name="Tile Layer",
                             items=[('A','A',''),('B','B',''),('C','C',''),('D','D',''),('E','E','')],
                             default='B')
    decimals: IntProperty(name="Decimals", default=2, min=0, max=6)
    target_map: EnumProperty(
        name="Target Map", items=_get_pkg_items,
        description="Map whose JSON note will be updated with camera values")


# ══════════════════════════════════════════════════════════════════════════════
# OPERATORS
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Camera Rig ─────────────────────────────────────────────────────────────

class R3ST_OT_setup_rig(Operator):
    bl_idname      = "r3st.setup_rig"
    bl_label       = "Create / Rebuild Rig"
    bl_description = "Delete and recreate R3ST camera rig with live-driven sliders"

    def execute(self, context):
        s = context.scene.r3st_setup

        cam_data = bpy.data.cameras.get(CAM_NAME)
        if cam_data:
            bpy.data.cameras.remove(cam_data)
        remove_obj(PIVOT_NAME); remove_obj(ARM_NAME); remove_obj(CAM_NAME)

        col = get_or_create_collection(COLLECTION_NAME)

        # Pivot — holds all camera sliders as custom properties
        pivot = bpy.data.objects.new(PIVOT_NAME, None)
        pivot.empty_display_type = 'SPHERE'
        pivot.empty_display_size = 0.25
        link_to_collection(pivot, col)

        add_custom_prop(pivot,'yaw',  _RIG_YAW,  -180.0,180.0,
                        '0=north(+Y)  90=west(-X)  -90=east(+X)  180=south(-Y)')
        add_custom_prop(pivot,'pitch',_RIG_PITCH,   0.0, 89.0,
                        '0=top-down  45=typical RPG  89≈horizontal')
        add_custom_prop(pivot,'dist', _RIG_DIST,    0.1, 50.0,
                        'Camera pull-back distance in tiles')
        add_custom_prop(pivot,'fov',  _RIG_FOV,     5.0,120.0,
                        'Vertical field of view in degrees')
        add_custom_prop(pivot,'roll', _RIG_ROLL, -180.0,180.0,
                        'Camera roll in degrees  (0 = level)')

        # Camera arm — carries position drivers + Track To constraint
        arm = bpy.data.objects.new(ARM_NAME, None)
        arm.empty_display_type = 'ARROWS'
        arm.empty_display_size = 0.1
        link_to_collection(arm, col)

        con = arm.constraints.new('TRACK_TO')
        con.target = pivot; con.track_axis = 'TRACK_NEGATIVE_Z'; con.up_axis = 'UP_Y'

        VARS = [('yaw','["yaw"]'),('pitch','["pitch"]'),('dist','["dist"]'),
                ('px','location.x'),('py','location.y'),('pz','location.z')]
        YR = f'yaw * {PI} / 180'
        PR = f'(pitch - 90) * {PI} / 180'

        add_driver(arm,'location',0,pivot,VARS,f'px + dist*sin({YR})*cos({PR})')
        add_driver(arm,'location',1,pivot,VARS,f'py - dist*cos({YR})*cos({PR})')
        add_driver(arm,'location',2,pivot,VARS,f'pz - dist*sin({PR})')

        # Camera — parents to arm, only adds roll on top
        cam_data = bpy.data.cameras.new(CAM_NAME)
        cam_data.lens_unit = 'MILLIMETERS'
        cam_data.sensor_fit = 'VERTICAL'
        cam_data.sensor_height = SENSOR_H
        cam_obj = bpy.data.objects.new(CAM_NAME, cam_data)
        link_to_collection(cam_obj, col)
        cam_obj.parent = arm
        cam_obj.rotation_mode = 'XYZ'

        add_driver(cam_obj,'rotation_euler',2,pivot,[('roll','["roll"]')],
                   f'roll * {PI} / 180')
        add_driver(cam_obj.data,'lens',-1,pivot,[('fov','["fov"]')],
                   f'({SENSOR_H/2}) / tan(fov * {PI} / 360)')
        # Ortho scale driven by dist + fov — matches OrthoGroups' frustum formula:
        # ortho_scale = 2 × dist × tan(fov/2 in radians)
        add_driver(cam_obj.data,'ortho_scale',-1,pivot,
                   [('dist','["dist"]'),('fov','["fov"]')],
                   '2 * dist * tan(fov * pi / 360)')

        context.scene.camera = cam_obj
        context.scene.render.resolution_x = s.res_x
        context.scene.render.resolution_y = s.res_y

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.region_3d.view_perspective = 'CAMERA'
                break

        self.report({'INFO'}, f"Rig created in '{COLLECTION_NAME}'")
        return {'FINISHED'}


# ── 2a. Generate Tile Images ──────────────────────────────────────────────────

class R3ST_OT_generate_tiles(Operator):
    bl_idname      = "r3st.generate_tiles"
    bl_label       = "1. Generate Tile Images"
    bl_description = "Write one PNG per floor tile into the output folder"

    def execute(self, context):
        s = context.scene.r3st_setup
        r = context.scene.r3st_room
        if not s.project_dir:
            self.report({'ERROR'}, "Set the Project Root in Scene Setup first.")
            return {'CANCELLED'}
        root    = bpy.path.abspath(s.project_dir)
        out_dir = os.path.join(root, '_R3ST')
        results = generate_tile_images(out_dir, r.room_w, r.room_d, s.tile_px,
                                       _ROOM_COLOR_A, _ROOM_COLOR_B,
                                       _ROOM_TEXT_COLOR, _ROOM_BORDER_COLOR)
        self.report({'INFO'}, f"{len(results)} tile PNGs → {out_dir}")
        return {'FINISHED'}


# ── 2b. Build Room ────────────────────────────────────────────────────────────

class R3ST_OT_build_demo_room(Operator):
    bl_idname      = "r3st.build_demo_room"
    bl_label       = "2. Create / Rebuild Room"
    bl_description = "Build scene room (floor uses per-tile PNGs, walls solid colour)"

    def execute(self, context):
        s   = context.scene.r3st_setup
        r   = context.scene.r3st_room
        col = get_or_create_collection(COLLECTION_NAME)

        if not s.project_dir:
            self.report({'ERROR'}, "Set the Project Root in Scene Setup first.")
            return {'CANCELLED'}
        out_dir = os.path.join(bpy.path.abspath(s.project_dir), '_R3ST')

        remove_obj(ROOM_NAME)
        for ci in range(r.room_w):
            for ri in range(r.room_d):
                remove_mat(f'R3ST_Floor_{tile_image_name(ci, ri)}')
        remove_mat(ROOM_NAME + '_WallNS'); remove_mat(ROOM_NAME + '_WallEW')

        mesh = bpy.data.meshes.new(ROOM_NAME)
        bm   = bmesh.new()
        uv_layer = bm.loops.layers.uv.new('UVMap')

        w = float(r.room_w); d = float(r.room_d); h = float(r.room_h)
        mat_slots = {}

        def get_or_add_slot(mat_name):
            if mat_name not in mat_slots:
                mat_slots[mat_name] = len(mat_slots)
            return mat_slots[mat_name]

        # Floor quads — bottom-left corner at origin (0,0,0)
        # X: 0 → room_w   Y: 0 → room_d
        for ci in range(r.room_w):
            for ri in range(r.room_d):
                x0 = float(ci); x1 = x0 + 1.0
                y0 = float(ri); y1 = y0 + 1.0
                v0=bm.verts.new((x0,y0,0)); v1=bm.verts.new((x1,y0,0))
                v2=bm.verts.new((x1,y1,0)); v3=bm.verts.new((x0,y1,0))
                face = bm.faces.new([v0,v1,v2,v3])
                for loop, uvc in zip(face.loops,
                                     [(0,0),(1,0),(1,1),(0,1)]):
                    loop[uv_layer].uv = uvc
                face.material_index = get_or_add_slot(
                    f'R3ST_Floor_{tile_image_name(ci, ri)}')

        # Walls — vertex order is reversed so normals point INWARD (into the room).
        # South wall  (y = 0, normal points +y)
        slot_ns = get_or_add_slot(ROOM_NAME + '_WallNS')
        for ci in range(r.room_w):
            x0=float(ci); x1=x0+1
            v0=bm.verts.new((x0,0,0)); v1=bm.verts.new((x1,0,0))
            v2=bm.verts.new((x1,0,h)); v3=bm.verts.new((x0,0,h))
            bm.faces.new([v3,v2,v1,v0]).material_index = slot_ns
        # North wall  (y = room_d, normal points -y)
        for ci in range(r.room_w):
            x0=float(ci); x1=x0+1
            v0=bm.verts.new((x1,d,0)); v1=bm.verts.new((x0,d,0))
            v2=bm.verts.new((x0,d,h)); v3=bm.verts.new((x1,d,h))
            bm.faces.new([v3,v2,v1,v0]).material_index = slot_ns

        slot_ew = get_or_add_slot(ROOM_NAME + '_WallEW')
        # West wall  (x = 0, normal points +x)
        for ri in range(r.room_d):
            y0=float(ri); y1=y0+1
            v0=bm.verts.new((0,y1,0)); v1=bm.verts.new((0,y0,0))
            v2=bm.verts.new((0,y0,h)); v3=bm.verts.new((0,y1,h))
            bm.faces.new([v3,v2,v1,v0]).material_index = slot_ew
        # East wall  (x = room_w, normal points -x)
        for ri in range(r.room_d):
            y0=float(ri); y1=y0+1
            v0=bm.verts.new((w,y0,0)); v1=bm.verts.new((w,y1,0))
            v2=bm.verts.new((w,y1,h)); v3=bm.verts.new((w,y0,h))
            bm.faces.new([v3,v2,v1,v0]).material_index = slot_ew

        bm.to_mesh(mesh); bm.free()
        room_obj = bpy.data.objects.new(ROOM_NAME, mesh)
        link_to_collection(room_obj, col)

        for mat_name, _ in sorted(mat_slots.items(), key=lambda x: x[1]):
            if mat_name == ROOM_NAME + '_WallNS':
                room_obj.data.materials.append(
                    _solid_emit_mat(mat_name, (0.08,0.30,0.20,1.0)))
            elif mat_name == ROOM_NAME + '_WallEW':
                room_obj.data.materials.append(
                    _solid_emit_mat(mat_name, (0.40,0.10,0.15,1.0)))
            else:
                label = mat_name[len('R3ST_Floor_'):]
                fpath = os.path.join(out_dir, label + '.png')
                if os.path.exists(fpath):
                    room_obj.data.materials.append(
                        _mat_from_image_path(mat_name, fpath))
                else:
                    ci = _COL_LETTERS.index(label[0]) if label[0] in _COL_LETTERS else 0
                    ri = int(label[1:]) if label[1:].isdigit() else 0
                    bg = _ROOM_COLOR_A if (ci+ri)%2==0 else _ROOM_COLOR_B
                    room_obj.data.materials.append(_solid_emit_mat(mat_name, bg))
                    print(f'[R3ST] WARNING: {fpath} not found — flat colour fallback.')

        # Unit cube
        remove_obj(CUBE_NAME); remove_mat(CUBE_NAME + '_Mat')
        cmesh = bpy.data.meshes.new(CUBE_NAME)
        cbm = bmesh.new(); bmesh.ops.create_cube(cbm, size=1.0)
        cbm.to_mesh(cmesh); cbm.free()
        cube = bpy.data.objects.new(CUBE_NAME, cmesh)
        cube.location = (1.0, 1.0, 0.5)
        link_to_collection(cube, col)
        cmat = bpy.data.materials.new(CUBE_NAME + '_Mat')
        cmat.use_nodes = True
        cmat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = \
            _ROOM_CUBE_COLOR
        cube.data.materials.append(cmat)

        # ── Auto-tag room as A5 0,0 OBJ ──────────────────────────────────────
        t = context.scene.r3st_tileset
        room_obj['r3st_sheet']        = 'A5'
        room_obj['r3st_col']          = 0
        room_obj['r3st_row']          = 0
        room_obj['r3st_export_type']  = 'OBJ'
        room_obj['r3st_export_group'] = ''
        room_obj['r3st_render_group'] = 1

        map_note = ''
        if t.tag_map != 'NONE':
            room_obj['r3st_map'] = t.tag_map
            if s.project_dir:
                data_dir = os.path.join(bpy.path.abspath(s.project_dir), 'data')
                if os.path.isdir(data_dir):
                    infos = _load_map_infos(data_dir)
                    _, entry = _find_map_by_name(infos, t.tag_map)
                    if entry:
                        map_path = os.path.join(data_dir, f'Map{entry["id"]:03d}.json')
                        if os.path.exists(map_path):
                            with open(map_path, 'r', encoding='utf-8') as fh:
                                map_data = json.load(fh)
                            map_data['note'] = _build_mz3d_note(t.tag_map)
                            with open(map_path, 'w', encoding='utf-8') as fh:
                                json.dump(map_data, fh, ensure_ascii=False)
                            map_note = f'  •  note written to Map{entry["id"]:03d}.json'

        self.report({'INFO'},
                    f"Room {r.room_w}×{r.room_d}×{r.room_h} tiles — "
                    f"parallax: {r.room_w*48}×{r.room_d*48} px{map_note}")
        return {'FINISHED'}


# ── 2c. Bake Map Image ────────────────────────────────────────────────────────

class R3ST_OT_bake_map(Operator):
    bl_idname      = "r3st.bake_map"
    bl_label       = "3. Bake Grid Map Image"
    bl_description = ("Composite all tile PNGs into one large image — "
                      "use as parallax background in RPG Maker to visualise the grid")

    def execute(self, context):
        s = context.scene.r3st_setup
        r = context.scene.r3st_room
        t = context.scene.r3st_tileset
        if not s.project_dir:
            self.report({'ERROR'}, "Set the Project Root in Scene Setup first.")
            return {'CANCELLED'}

        root         = bpy.path.abspath(s.project_dir)
        tiles_dir    = os.path.join(root, '_R3ST')
        parallax_dir = os.path.join(root, 'img', 'parallaxes')

        if not os.path.isdir(parallax_dir):
            self.report({'ERROR'},
                        f"Parallax folder not found: {parallax_dir}")
            return {'CANCELLED'}

        map_name = t.map_name.strip() or 'grid_map'
        out_path = bake_map_image(
            tiles_dir, r.room_w, r.room_d, s.tile_px,
            _ROOM_COLOR_A, _ROOM_COLOR_B,
            _ROOM_TEXT_COLOR, _ROOM_BORDER_COLOR,
            save_path=os.path.join(parallax_dir, f'{map_name}.png'))

        iw = r.room_w * s.tile_px
        ih = r.room_d * s.tile_px
        self.report({'INFO'}, f"Baked {iw}×{ih} px → {out_path}")
        print(f"[R3ST] Baked grid map: {iw}×{ih} px → {out_path}")
        return {'FINISHED'}


# ── 3. Export Camera ──────────────────────────────────────────────────────────

class R3ST_OT_export_camera(Operator):
    bl_idname      = "r3st.export_camera"
    bl_label       = "Export & Copy to Clipboard"
    bl_description = "Read pivot sliders → copy RPG Maker Script block + mz3d-tiles notetag"

    def execute(self, context):
        e   = context.scene.r3st_export
        dec = e.decimals

        pivot = bpy.data.objects.get(PIVOT_NAME)
        cam   = bpy.data.objects.get(CAM_NAME)

        if pivot is None:
            self.report({'ERROR'}, f'"{PIVOT_NAME}" not found — run Create Rig first.')
            return {'CANCELLED'}
        if cam is None:
            self.report({'ERROR'}, f'"{CAM_NAME}" not found — run Create Rig first.')
            return {'CANCELLED'}

        yaw    = round(pivot.get('yaw',   0.0),  dec)
        pitch  = round(pivot.get('pitch', 45.0), dec)
        dist   = round(pivot.get('dist',  8.0),  dec)
        roll   = round(pivot.get('roll',  0.0),  dec)
        height = round(pivot.matrix_world.translation.z, dec)

        lens     = cam.data.lens
        sensor_h = cam.data.sensor_height
        fov_deg  = round(math.degrees(2.0 * math.atan(sensor_h / (2.0 * lens))), dec)
        zoom     = round(
            math.tan(math.radians(e.mz3d_default_fov / 2.0)) /
            math.tan(math.radians(fov_deg            / 2.0)), dec)

        script = (
            f'// yaw={yaw}  pitch={pitch}  dist={dist}  '
            f'height={height}  roll={roll}  fov={fov_deg}  zoom={zoom}\n'
            f'mv3d.blendCameraYaw.setValue({yaw}, 0);\n'
            f'mv3d.blendCameraPitch.setValue({pitch}, 0);\n'
            f'mv3d.blendCameraDist.setValue({dist}, 0);\n'
            f'mv3d.blendCameraHeight.setValue({height}, 0);\n'
            f'mv3d.blendCameraRoll.setValue({roll}, 0);\n'
            f'mv3d.blendCameraZoom.setValue({zoom}, 0);'
        )

        pw  = pivot.matrix_world.translation
        bx, by, bz = pw.x, pw.y, pw.z
        col_idx = round(bx); row_idx = -round(by)
        xoff = round(bx - round(bx), dec)
        yoff = round(round(by) - by, dec)
        zoff = round(bz, dec)

        tile_entry = (
            f'{e.tile_layer},{col_idx},{row_idx}:'
            f'model(YOUR_MODEL.obj),'
            f'xoff({xoff}),yoff({yoff}),zoff({zoff}),'
            f'renderGroup(0),climb(false)'
        )
        tile_block = (
            f'<mz3d-tiles>\n{tile_entry}\n</mz3d-tiles>\n'
            f'// origin: tile (0,0) = Blender (0,0,0)\n'
            f'// pivot pos: x={round(bx,dec)}  y={round(by,dec)}  z={round(bz,dec)}'
        )

        context.window_manager.clipboard = f'{script}\n\n{tile_block}'
        print('─' * 60)
        print('[R3ST] ── CAMERA'); print(script)
        print('[R3ST] ── TILES');  print(tile_block)
        print('─' * 60)

        # ── Write <mz3d> camera block to map JSON ─────────────────────────────
        s          = context.scene.r3st_setup
        map_name   = e.target_map
        map_written = False

        if map_name and map_name != 'NONE' and s.project_dir:
            data_dir = os.path.join(bpy.path.abspath(s.project_dir), 'data')
            if os.path.isdir(data_dir):
                infos = _load_map_infos(data_dir)
                _, entry = _find_map_by_name(infos, map_name)
                if entry:
                    map_path = os.path.join(data_dir, f'Map{entry["id"]:03d}.json')
                    if os.path.exists(map_path):
                        with open(map_path, 'r', encoding='utf-8') as fh:
                            map_data = json.load(fh)

                        cam_block = (
                            f'<mz3d>\n'
                            f'camera({yaw},{pitch}|{dist}|{height}|{e.camera_mode})\n'
                            f'</mz3d>'
                        )
                        # Replace existing <mz3d>...</mz3d> block or prepend
                        note = map_data.get('note', '')
                        note = re.sub(r'<mz3d>.*?</mz3d>', '', note,
                                      flags=re.DOTALL).strip()
                        map_data['note'] = (cam_block + '\n\n' + note).strip()

                        with open(map_path, 'w', encoding='utf-8') as fh:
                            json.dump(map_data, fh, ensure_ascii=False)
                        map_written = True
                        print(f'[R3ST] Camera → Map{entry["id"]:03d}.json  ("{map_name}")')

        if map_written:
            self.report({'INFO'}, f"Copied to clipboard  •  camera written to '{map_name}'")
        else:
            self.report({'INFO'}, "Copied to clipboard!")
        return {'FINISHED'}


# ── 4. Generate Tileset Sheets ────────────────────────────────────────────────

class R3ST_OT_generate_tilesets(Operator):
    bl_idname      = "r3st.generate_tilesets"
    bl_label       = "Generate Tileset Sheets"
    bl_description = ("Write R3ST_{name}_A5/B/C/D/E.png into img/tilesets/ "
                      "and create a map entry in data/MapInfos.json")

    _conflicts: list = []   # class-level, set in invoke, read in draw

    def _find_conflicts(self, context):
        s  = context.scene.r3st_setup
        t  = context.scene.r3st_tileset
        conflicts = []
        if not s.project_dir:
            return conflicts
        root     = bpy.path.abspath(s.project_dir)
        ts_dir   = os.path.join(root, 'img', 'tilesets')
        data_dir = os.path.join(root, 'data')

        # PNGs
        for sheet in _SHEET_DEFS:
            fpath = os.path.join(ts_dir, f'R3ST_{t.map_name}_{sheet}.png')
            if os.path.exists(fpath):
                conflicts.append(f'img/tilesets/R3ST_{t.map_name}_{sheet}.png')

        # MapInfos entry + map JSON
        if os.path.isdir(data_dir):
            infos = _load_map_infos(data_dir)
            _, entry = _find_map_by_name(infos, t.map_name)
            if entry:
                conflicts.append(f'MapInfos.json  →  "{t.map_name}"')
                conflicts.append(f'data/Map{entry["id"]:03d}.json')

            # Tilesets.json entry
            ts_path = os.path.join(data_dir, 'Tilesets.json')
            if os.path.exists(ts_path):
                tilesets = _load_tilesets(data_dir)
                _, ts_entry = _find_tileset_by_name(tilesets, t.map_name)
                if ts_entry:
                    conflicts.append(f'Tilesets.json  →  "{t.map_name}"  (id={ts_entry["id"]})')
        return conflicts

    def invoke(self, context, event):
        s = context.scene.r3st_setup
        t = context.scene.r3st_tileset
        if not s.project_dir:
            self.report({'ERROR'}, "Set Project Root in Scene Setup first.")
            return {'CANCELLED'}
        if not t.map_name.strip():
            self.report({'ERROR'}, "Map Name cannot be empty.")
            return {'CANCELLED'}
        conflicts = self._find_conflicts(context)
        if conflicts:
            R3ST_OT_generate_tilesets._conflicts = conflicts
            return context.window_manager.invoke_props_dialog(self, width=440)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        if R3ST_OT_generate_tilesets._conflicts:
            layout.label(text="These already exist and will be overwritten:", icon='ERROR')
            col = layout.column(align=True)
            for c in R3ST_OT_generate_tilesets._conflicts:
                col.label(text=f"  •  {c}")
            layout.separator(factor=0.5)
        layout.label(text="Click OK to proceed, Cancel to abort.")

    def execute(self, context):
        R3ST_OT_generate_tilesets._conflicts = []
        s = context.scene.r3st_setup
        t = context.scene.r3st_tileset

        if not s.project_dir:
            self.report({'ERROR'}, "Set Project Root in Scene Setup first.")
            return {'CANCELLED'}

        root     = bpy.path.abspath(s.project_dir)
        ts_dir   = os.path.join(root, 'img', 'tilesets')
        data_dir = os.path.join(root, 'data')

        if not os.path.isdir(ts_dir):
            self.report({'ERROR'}, f"Tilesets folder not found: {ts_dir}")
            return {'CANCELLED'}

        # ── 1. Generate PNGs ──────────────────────────────────────────────────
        written = generate_tileset_sheets(ts_dir, t.map_name, t.active_count, s.tile_px)

        # ── 2. MapInfos.json + MapXXX.json ────────────────────────────────────
        map_id   = None
        map_file = None

        if os.path.isdir(data_dir):
            infos = _load_map_infos(data_dir)
            _, existing = _find_map_by_name(infos, t.map_name)

            if existing:
                map_id = existing['id']
            else:
                map_id    = _next_map_id(infos)
                map_order = _next_map_order(infos)
                new_entry = {
                    "id": map_id, "expanded": False,
                    "name": t.map_name, "order": map_order,
                    "parentId": 0,
                    "scrollX": 680.0, "scrollY": 391.6666666666667,
                }
                while len(infos) <= map_id:
                    infos.append(None)
                infos[map_id] = new_entry
                _save_map_infos(data_dir, infos)

            # Build note from tagged objects and write map JSON
            note     = _build_mz3d_note(t.map_name)
            map_file = f'Map{map_id:03d}.json'
            map_path = os.path.join(data_dir, map_file)
            with open(map_path, 'w', encoding='utf-8') as fh:
                json.dump(_make_blank_map(17, 13, note), fh, ensure_ascii=False)

            # ── 3. Tilesets.json ──────────────────────────────────────────────
            ts_json_path = os.path.join(data_dir, 'Tilesets.json')
            ts_written   = False
            ts_slots_filled = 0
            if os.path.exists(ts_json_path):
                tilesets    = _load_tilesets(data_dir)
                ts_names    = _build_tileset_names(ts_dir, t.map_name)
                ts_slots_filled = sum(1 for n in ts_names if n)
                _, ts_entry = _find_tileset_by_name(tilesets, t.map_name)

                if ts_entry:
                    # Overwrite existing entry's tilesetNames in-place
                    ts_entry['tilesetNames'] = ts_names
                else:
                    # Append new entry
                    new_ts_id = _next_tileset_id(tilesets)
                    new_entry = _make_blank_tileset_entry(new_ts_id, t.map_name, ts_names)
                    tilesets.append(new_entry)

                _save_tilesets(data_dir, tilesets)
                ts_written = True

            tag_count = sum(
                1 for o in bpy.data.objects
                if o.type == 'MESH' and o.get('r3st_map') == t.map_name)

            ts_note = (f"  •  Tilesets.json ({ts_slots_filled} sheet(s) linked)"
                       if ts_written else "  •  Tilesets.json not found, skipped")
            self.report({'INFO'},
                        f"Sheets ×{len(written)}  •  {map_file}  "
                        f"({tag_count} tagged object(s) in note){ts_note}  →  '{t.map_name}'")
        else:
            self.report({'INFO'},
                        f"Generated {len(written)} sheets — data/ not found, skipped map/tileset creation")
        return {'FINISHED'}


# ── 5. Tag Geometry ───────────────────────────────────────────────────────────

class R3ST_OT_tag_geometry(Operator):
    bl_idname      = "r3st.tag_geometry"
    bl_label       = "Tag Selected"
    bl_description = ("Write r3st_map / r3st_sheet / r3st_col / r3st_row / r3st_render_group "
                      "onto every selected mesh object")

    _map_exists: bool = False
    _existing_map: str = ''

    def invoke(self, context, event):
        t = context.scene.r3st_tileset
        r = context.scene.r3st_room

        if t.tag_map == 'NONE':
            self.report({'ERROR'}, "No map selected — generate tileset sheets first.")
            return {'CANCELLED'}

        # Check if map already has a data entry
        R3ST_OT_tag_geometry._map_exists = False
        s = context.scene.r3st_setup
        if s.project_dir:
            data_dir = os.path.join(bpy.path.abspath(s.project_dir), 'data')
            if os.path.isdir(data_dir):
                infos = _load_map_infos(data_dir)
                _, entry = _find_map_by_name(infos, t.tag_map)
                if entry:
                    R3ST_OT_tag_geometry._map_exists = True
                    R3ST_OT_tag_geometry._existing_map = (
                        f'Map{entry["id"]:03d}.json  ("{t.tag_map}")')

        if R3ST_OT_tag_geometry._map_exists:
            return context.window_manager.invoke_props_dialog(self, width=420)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(
            text=f'Map "{R3ST_OT_tag_geometry._existing_map}" already exists.',
            icon='INFO')
        layout.label(text="Tagging will update its mz3d-tiles note immediately.")
        layout.label(text="Click OK to proceed.")

    def execute(self, context):
        R3ST_OT_tag_geometry._map_exists = False
        s     = context.scene.r3st_setup
        t     = context.scene.r3st_tileset
        r     = context.scene.r3st_room
        m            = t.tag_map
        sheet        = t.tag_sheet
        col          = t.tag_col
        row          = t.tag_row
        export_type  = t.tag_export_type
        export_group = t.tag_export_group.strip()

        if m == 'NONE':
            self.report({'ERROR'}, "No map selected — generate tileset sheets first.")
            return {'CANCELLED'}

        # ── 1. Write custom props onto selected objects and rename ─────────────
        tagged = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            obj['r3st_map']          = m
            obj['r3st_sheet']        = sheet
            obj['r3st_col']          = col
            obj['r3st_row']          = row
            obj['r3st_render_group'] = int(t.tag_render_group)
            obj['r3st_export_type']  = export_type
            obj['r3st_export_group'] = export_group

            # Rename: strip any existing [group] prefix then prepend new one
            base = re.sub(r'^\[.*?\]\s*', '', obj.name)
            obj.name = f'[{export_group}] {base}' if export_group else base
            tagged += 1

        if tagged == 0:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        # ── 2. Rebuild mz3d-tiles note and write to MapXXX.json ───────────────
        # _build_mz3d_note scans ALL scene objects tagged to this map,
        # so the note always reflects the complete current state.
        map_file    = None
        map_updated = False

        if s.project_dir:
            data_dir = os.path.join(bpy.path.abspath(s.project_dir), 'data')
            if os.path.isdir(data_dir):
                infos = _load_map_infos(data_dir)
                _, entry = _find_map_by_name(infos, m)
                if entry:
                    map_id   = entry['id']
                    map_file = f'Map{map_id:03d}.json'
                    map_path = os.path.join(data_dir, map_file)
                    if os.path.exists(map_path):
                        with open(map_path, 'r', encoding='utf-8') as fh:
                            map_data = json.load(fh)
                        map_data['note'] = _build_mz3d_note(m)
                        with open(map_path, 'w', encoding='utf-8') as fh:
                            json.dump(map_data, fh, ensure_ascii=False)
                        map_updated = True

        if map_updated:
            total_tagged = sum(
                1 for o in bpy.data.objects
                if o.type == 'MESH' and o.get('r3st_map') == m)
            self.report({'INFO'},
                        f"Tagged {tagged} object(s)  •  {map_file} note updated "
                        f"({total_tagged} total bindings)  →  {m}")
        else:
            hint = "run Generate Sheets first" if s.project_dir else "set project root first"
            self.report({'INFO'},
                        f"Tagged {tagged} object(s)  →  {m} : {sheet} {col},{row}  "
                        f"(map JSON not written — {hint})")
        return {'FINISHED'}


# ── 6. Prepare for Export ────────────────────────────────────────────────────

class R3ST_OT_prepare_export(Operator):
    bl_idname      = "r3st.prepare_export"
    bl_label       = "4. Prepare for Export"
    bl_description = ("Weld coincident vertices, UV-unwrap the room using a top-down "
                      "planar projection, and assign the baked parallax map as a single "
                      "Principled BSDF material ready for .obj export")

    def execute(self, context):
        s = context.scene.r3st_setup
        r = context.scene.r3st_room
        t = context.scene.r3st_tileset

        room_obj = bpy.data.objects.get(ROOM_NAME)
        if room_obj is None:
            self.report({'ERROR'}, f'"{ROOM_NAME}" not found — build the room first.')
            return {'CANCELLED'}

        if not s.project_dir:
            self.report({'ERROR'}, "Set the Project Root in Scene Setup first.")
            return {'CANCELLED'}

        root          = bpy.path.abspath(s.project_dir)
        map_name      = t.map_name.strip() or 'grid_map'
        parallax_path = os.path.join(root, 'img', 'parallaxes', f'{map_name}.png')
        has_texture   = os.path.exists(parallax_path)

        if not has_texture:
            self.report({'WARNING'},
                        f"Parallax map not found at {parallax_path} — "
                        "run 'Bake Grid Map Image' first. Continuing without texture.")

        mesh = room_obj.data

        # ── 1. Weld coincident vertices ───────────────────────────────────────
        bm = bmesh.new()
        bm.from_mesh(mesh)
        verts_before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
        verts_after  = len(bm.verts)

        # ── 2. Top-down planar UV projection ──────────────────────────────────
        # u = world_x / room_w,  v = world_y / room_d
        # Each floor tile maps exactly to its cell in the parallax grid.
        uv_name = 'R3ST_UV'

        # Remove all existing UV layers so only R3ST_UV remains
        for layer in list(bm.loops.layers.uv.values()):
            if layer.name != uv_name:
                bm.loops.layers.uv.remove(layer)

        uv_layer = (bm.loops.layers.uv.get(uv_name)
                    or bm.loops.layers.uv.new(uv_name))

        mat_world = room_obj.matrix_world
        for face in bm.faces:
            for loop in face.loops:
                world_co = mat_world @ loop.vert.co
                u = world_co.x / r.room_w
                v = world_co.y / r.room_d
                loop[uv_layer].uv = (u, v)

        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

        # ── 3. Material with parallax texture ─────────────────────────────────
        mat_name = f'R3ST_{map_name}'
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(mat_name)

        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        out_node  = nodes.new('ShaderNodeOutputMaterial')
        out_node.location = (300, 0)

        bsdf_node = nodes.new('ShaderNodeBsdfPrincipled')
        bsdf_node.location = (0, 0)
        links.new(bsdf_node.outputs['BSDF'], out_node.inputs['Surface'])

        if has_texture:
            img = bpy.data.images.get(os.path.basename(parallax_path))
            if img is None:
                img = bpy.data.images.load(parallax_path)

            uv_node = nodes.new('ShaderNodeUVMap')
            uv_node.uv_map  = uv_name
            uv_node.location = (-600, 0)

            tex_node = nodes.new('ShaderNodeTexImage')
            tex_node.image    = img
            tex_node.location = (-300, 0)

            links.new(uv_node.outputs['UV'],      tex_node.inputs['Vector'])
            links.new(tex_node.outputs['Color'],  bsdf_node.inputs['Base Color'])

        # Replace all materials on the room with this single one
        mesh.materials.clear()
        mesh.materials.append(mat)

        # Report
        merged   = verts_before - verts_after
        tex_note = (f"texture: {os.path.basename(parallax_path)}"
                    if has_texture else "no texture assigned (bake first)")
        self.report({'INFO'},
                    f"Welded {merged} vert(s)  •  UV map: {uv_name}  •  {tex_note}")
        return {'FINISHED'}


# ── 7. Export Level ───────────────────────────────────────────────────────────

class R3ST_OT_export_level(Operator):
    bl_idname      = "r3st.export_level"
    bl_label       = "Export Level"
    bl_description = ("Export R3ST_Room as .obj, then export every tagged export group "
                      "as .obj or .glb — all into {project_root}/models/")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _select_only(self, context, objs):
        bpy.ops.object.select_all(action='DESELECT')
        for o in objs:
            o.select_set(True)
        if objs:
            context.view_layer.objects.active = objs[0]

    def _export_obj(self, context, objs, out_path):
        self._select_only(context, objs)
        bpy.ops.wm.obj_export(
            filepath                = out_path,
            export_selected_objects = True,
            export_uv               = True,
            export_normals          = True,
            export_materials        = True,
            path_mode               = 'RELATIVE',
        )

    def _export_glb(self, context, objs, out_path):
        self._select_only(context, objs)
        bpy.ops.export_scene.gltf(
            filepath       = out_path,
            use_selection  = True,
            export_format  = 'GLB',
        )

    # ── main ─────────────────────────────────────────────────────────────────

    def execute(self, context):
        s = context.scene.r3st_setup

        if not s.project_dir:
            self.report({'ERROR'}, "Set the Project Root in Scene Setup first.")
            return {'CANCELLED'}

        root       = bpy.path.abspath(s.project_dir)
        models_dir = os.path.join(root, 'models')
        os.makedirs(models_dir, exist_ok=True)

        exported = []

        # ── 1. R3ST_Room → always .obj ────────────────────────────────────────
        room_obj = bpy.data.objects.get(ROOM_NAME)
        if room_obj:
            out_path = os.path.join(models_dir, room_obj.name + '.obj')
            self._export_obj(context, [room_obj], out_path)
            exported.append(room_obj.name + '.obj')
            print(f'[R3ST] Room   → {out_path}')
        else:
            self.report({'WARNING'}, f'"{ROOM_NAME}" not found — skipping room export.')

        # ── 2. Collect export groups from tagged scene objects ─────────────────
        groups = {}   # (group_name, export_type) → [obj, ...]
        for obj in bpy.data.objects:
            if obj.type != 'MESH' or obj.name == ROOM_NAME:
                continue
            grp = obj.get('r3st_export_group', '').strip()
            ext = obj.get('r3st_export_type', 'GLB')
            if not grp:
                continue
            groups.setdefault((grp, ext), []).append(obj)

        # ── 3. Export each group ──────────────────────────────────────────────
        for (grp, ext), objs in sorted(groups.items()):
            filename = f'{grp}.{ext.lower()}'
            out_path = os.path.join(models_dir, filename)
            try:
                if ext == 'OBJ':
                    self._export_obj(context, objs, out_path)
                else:
                    self._export_glb(context, objs, out_path)
                exported.append(filename)
                print(f'[R3ST] Group  → {out_path}  ({len(objs)} object(s))')
            except Exception as ex:
                self.report({'WARNING'}, f'Failed to export "{filename}": {ex}')
                print(f'[R3ST] ERROR exporting "{filename}": {ex}')

        bpy.ops.object.select_all(action='DESELECT')

        self.report({'INFO'},
                    f"Exported {len(exported)} file(s) → models/  •  "
                    + ',  '.join(exported))
        return {'FINISHED'}


# ══════════════════════════════════════════════════════════════════════════════
# PANELS
# ══════════════════════════════════════════════════════════════════════════════

# Helper — draws a bold section header with a coloured accent line underneath
def _section_header(layout, text, icon='NONE'):
    col = layout.column(align=True)
    col.label(text=text, icon=icon)
    col.separator(factor=0.3)
    return layout


class R3ST_PT_main(Panel):
    bl_label       = "RMMZ 3D Scene Toolkit"
    bl_idname      = "R3ST_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'R3ST'

    # ── Tab content helpers ───────────────────────────────────────────────────

    @staticmethod
    def _draw_setup(context, layout):
        s = context.scene.r3st_setup
        box = layout.box()
        bc  = box.column(align=True)
        bc.prop(s, 'project_dir')
        bc.separator(factor=0.8)
        bc.prop(s, 'tile_px')
        bc.separator(factor=0.5)
        res_row = bc.row(align=True)
        res_row.prop(s, 'res_x', text="Res W")
        res_row.prop(s, 'res_y', text="Res H")

    @staticmethod
    def _draw_tilesets(context, layout):
        s = context.scene.r3st_setup
        t = context.scene.r3st_tileset
        _refresh_pkg_cache(s.project_dir)
        gen_box = layout.box()
        gen_box.label(text="Generate Sheets", icon='IMAGE_DATA')
        gc = gen_box.column(align=True)
        gc.prop(t, 'map_name')
        gc.prop(t, 'active_count')
        gc.separator(factor=0.5)
        if s.project_dir:
            gc.label(text=f"→ img/tilesets/R3ST_{t.map_name}_*.png", icon='FILE')
            gc.label(text="→ data/MapInfos.json  +  MapXXX.json",    icon='FILE')
            gc.label(text="Sheets: A5  B  C  D  E",                  icon='TEXTURE')
        else:
            gc.label(text="Set Project Root in Scene Setup first.", icon='INFO')
        gc.separator(factor=0.5)
        gc.operator("r3st.generate_tilesets", icon='IMAGE_DATA')

    @staticmethod
    def _draw_geo_tag(context, layout):
        s = context.scene.r3st_setup
        t = context.scene.r3st_tileset
        _refresh_pkg_cache(s.project_dir)
        tag_box = layout.box()
        tag_box.label(text="Tag Geometry", icon='MESH_DATA')
        tc = tag_box.column(align=True)
        tc.prop(t, 'tag_map')
        tc.prop(t, 'tag_sheet')
        cr = tc.row(align=True)
        cr.prop(t, 'tag_col')
        cr.prop(t, 'tag_row')
        tc.prop(t, 'tag_render_group')
        tc.separator(factor=0.5)
        er = tc.row(align=True)
        er.prop(t, 'tag_export_type', expand=True)
        tc.prop(t, 'tag_export_group')
        _RG_PROJ = {'0':'ORTHO','1':'ORTHO','2':'PERSP','3':'ORTHO'}
        if t.tag_map != 'NONE':
            tc.separator(factor=0.5)
            grp_preview = f'[{t.tag_export_group}]  ' if t.tag_export_group.strip() else ''
            proj = _RG_PROJ.get(t.tag_render_group, 'ORTHO')
            tc.label(
                text=(f"{grp_preview}R3ST_{t.tag_map}_{t.tag_sheet}"
                      f"  :  {t.tag_col},{t.tag_row}"
                      f"  rg={t.tag_render_group} [{proj}]  →  .{t.tag_export_type.lower()}"),
                icon='CHECKMARK')
        tc.separator(factor=0.8)
        tc.operator("r3st.tag_geometry", icon='OBJECT_DATAMODE')
        obj = context.active_object
        if obj and obj.type == 'MESH':
            read_box = tag_box.box()
            read_box.label(text=f"Active:  {obj.name}", icon='OBJECT_DATA')
            if 'r3st_map' in obj:
                rc = read_box.column(align=True)
                grp  = obj.get('r3st_export_group', '')
                ext  = obj.get('r3st_export_type', 'GLB').lower()
                rg   = str(obj.get('r3st_render_group', 1))
                proj = _RG_PROJ.get(rg, 'ORTHO')
                rc.label(text=(f"  R3ST_{obj['r3st_map']}_{obj['r3st_sheet']}"
                               f"  :  {obj['r3st_col']},{obj['r3st_row']}"
                               f"  rg={rg} [{proj}]"),
                         icon='LINKED')
                rc.label(text=(f"  [{grp}]  →  .{ext}" if grp else f"  (no group)  →  .{ext}"),
                         icon='PACKAGE')
            else:
                read_box.label(text="  No tag", icon='UNLINKED')

    @staticmethod
    def _draw_generator(context, layout):
        s = context.scene.r3st_setup
        r = context.scene.r3st_room
        # Dimensions
        dim_box = layout.box()
        dim_box.label(text="Dimensions  (= RPG Maker map size)", icon='GRID')
        dc = dim_box.column(align=True)
        dr = dc.row(align=True)
        dr.prop(r, 'room_w', text="W (X)")
        dr.prop(r, 'room_d', text="D (Y)")
        dr.prop(r, 'room_h', text="H (Z)")
        dc.separator(factor=0.5)
        px_w = r.room_w * s.tile_px; px_h = r.room_d * s.tile_px
        dc.label(text=f"Parallax / render:  {px_w} × {px_h} px")
        dc.label(text=f"Cols A–{col_letter(r.room_w-1)}    Rows 0–{r.room_d-1}")
        layout.separator(factor=1.0)
        # Actions
        act_box = layout.box()
        act_box.label(text="Tiles → _R3ST/    Map → img/parallaxes/", icon='INFO')
        act_box.separator(factor=0.3)
        act_box.operator("r3st.generate_tiles",  icon='EXPORT')
        act_box.operator("r3st.build_demo_room", icon='MESH_GRID')
        act_box.separator(factor=0.5)
        act_box.operator("r3st.bake_map",        icon='IMAGE_BACKGROUND')
        act_box.separator(factor=0.5)
        act_box.operator("r3st.prepare_export",  icon='NORMALS_FACE')
        layout.separator(factor=1.0)

    @staticmethod
    def _draw_export(context, layout):
        s = context.scene.r3st_setup
        e = context.scene.r3st_export
        _refresh_pkg_cache(s.project_dir)
        # Camera export
        cam_box = layout.box()
        cam_box.label(text="Camera", icon='CAMERA_DATA')
        cc = cam_box.column(align=True)
        cc.prop(e, 'mz3d_default_fov')
        cc.prop(e, 'camera_mode')
        cc.prop(e, 'decimals')
        cc.separator(factor=0.8)
        cc.prop(e, 'target_map')
        pivot = bpy.data.objects.get(PIVOT_NAME)
        if pivot:
            cc.separator(factor=0.5)
            vc = cc.box().column(align=True)
            vc.label(text=f"yaw={round(pivot.get('yaw',0.0),2)}  "
                          f"pitch={round(pivot.get('pitch',45.0),2)}  "
                          f"dist={round(pivot.get('dist',9.0),2)}")
            vc.label(text=f"fov={round(pivot.get('fov',45.0),2)}  "
                          f"roll={round(pivot.get('roll',0.0),2)}")
        else:
            cc.label(text="Run Camera Rig first.", icon='ERROR')
        cc.separator(factor=0.5)
        cc.operator("r3st.export_camera", icon='COPYDOWN')
        layout.separator(factor=1.5)
        # Export level
        lvl_box = layout.box()
        lvl_box.label(text="Export Level", icon='EXPORT')
        lc = lvl_box.column(align=True)
        if s.project_dir:
            room_obj = bpy.data.objects.get(ROOM_NAME)
            obj_name = room_obj.name if room_obj else ROOM_NAME
            lc.label(text=f"  {obj_name}.obj  (room — always)", icon='FILE')
            groups = {}
            for obj in bpy.data.objects:
                if obj.type != 'MESH' or obj.name == ROOM_NAME:
                    continue
                grp = obj.get('r3st_export_group', '').strip()
                ext = obj.get('r3st_export_type', 'GLB')
                if grp:
                    groups.setdefault((grp, ext), 0)
                    groups[(grp, ext)] += 1
            if groups:
                lc.separator(factor=0.3)
                for (grp, ext), count in sorted(groups.items()):
                    lc.label(text=f"  {grp}.{ext.lower()}  ({count} object(s))", icon='FILE')
        else:
            lc.label(text="Set Project Root in Scene Setup first.", icon='INFO')
        lc.separator(factor=0.5)
        lc.operator("r3st.export_level", icon='MESH_DATA')

    @staticmethod
    def _draw_preview(context, layout):
        s = context.scene.r3st_setup

        # ── Camera Rig ────────────────────────────────────────────────────────
        rig_box = layout.box()
        rig_box.label(text="Camera Rig", icon='CAMERA_DATA')
        rig_box.operator("r3st.setup_rig", icon='CAMERA_DATA')
        rig_box.separator(factor=0.5)
        pivot   = bpy.data.objects.get(PIVOT_NAME)
        cam_obj = bpy.data.objects.get(CAM_NAME)
        if pivot and cam_obj:
            live_box = rig_box.box()
            live_box.label(text="Live Camera Controls", icon='DRIVER')
            lc = live_box.column(align=True)
            lc.prop(pivot, '["yaw"]',   text="Yaw")
            lc.prop(pivot, '["pitch"]', text="Pitch")
            lc.prop(pivot, '["dist"]',  text="Dist")
            lc.prop(pivot, '["fov"]',   text="FOV")
            lc.prop(pivot, '["roll"]',  text="Roll")
            lc.separator(factor=0.8)
            proj_row = lc.row(align=True)
            proj_row.prop_enum(cam_obj.data, 'type', 'PERSP')
            proj_row.prop_enum(cam_obj.data, 'type', 'ORTHO')
        else:
            rig_box.label(text="Create the rig to see live controls.", icon='INFO')

        layout.separator(factor=1.0)

        # ── Pipeline info ─────────────────────────────────────────────────────
        info_box = layout.box()
        info_box.label(text="Ortho BG + Persp Characters", icon='CAMERA_STEREO')
        ic = info_box.column(align=True)
        ic.label(text="Pass 1 — ortho cam, chars hidden  → BG",    icon='HIDE_OFF')
        ic.label(text="Pass 2 — persp cam, chars only    → Chars", icon='HIDE_ON')
        ic.label(text="Python Alpha Over → R3ST_Preview image",     icon='IMAGE_DATA')
        layout.separator(factor=1.2)
        cfg_box = layout.box()
        cfg_box.label(text="Configuration", icon='SETTINGS')
        cfg_box.prop(s, 'persp_collection')
        cfg_box.row(align=True).prop(s, 'preview_pct', expand=True)
        rig_ok = bool(bpy.data.objects.get(CAM_NAME))
        col_ok = bool(bpy.data.collections.get(s.persp_collection.strip() or 'Characters'))
        sc_box = cfg_box.box().column(align=True)
        sc_box.label(
            text=f"Rig:               {'found' if rig_ok else 'missing — create rig first'}",
            icon='CHECKMARK' if rig_ok else 'ERROR')
        sc_box.label(
            text=f"Persp collection:  {'found' if col_ok else 'missing — create in Outliner'}",
            icon='CHECKMARK' if col_ok else 'ERROR')
        layout.separator(factor=1.2)
        ready = rig_ok and col_ok
        act_box = layout.box()
        row = act_box.row()
        row.enabled = ready
        row.operator("r3st.render_preview", text="Render Preview", icon='RENDER_STILL')
        cont_active = _r3st_continuous['active']
        row2 = act_box.row()
        row2.enabled = ready or cont_active
        row2.alert   = cont_active
        row2.operator(
            "r3st.continuous_render",
            text="Stop Continuous" if cont_active else "Continuous Render",
            icon='PAUSE'          if cont_active else 'REC',
        )

    # ── Main draw ─────────────────────────────────────────────────────────────

    def draw(self, context):
        layout = self.layout
        s      = context.scene.r3st_setup

        layout.label(text="v1.4  ·  Claude.ai & Dimanology", icon='INFO')
        layout.separator(factor=0.4)

        # Vertical icon tab strip (left) + content (right)
        split    = layout.split(factor=0.18)
        col_tabs = split.column()
        col_tabs.scale_y = 1.5
        col_tabs.prop_enum(s, 'active_tab', 'SETUP',     text='', icon='SCENE_DATA')
        col_tabs.prop_enum(s, 'active_tab', 'TILESETS',  text='', icon='IMAGE_DATA')
        col_tabs.prop_enum(s, 'active_tab', 'GEO_TAG',   text='', icon='OBJECT_DATAMODE')
        col_tabs.prop_enum(s, 'active_tab', 'GENERATOR', text='', icon='GRID')
        col_tabs.prop_enum(s, 'active_tab', 'EXPORT',    text='', icon='EXPORT')
        col_tabs.prop_enum(s, 'active_tab', 'PREVIEW',   text='', icon='RENDER_STILL')

        col_content = split.column()
        tab = s.active_tab
        if   tab == 'SETUP':     self._draw_setup(context, col_content)
        elif tab == 'TILESETS':  self._draw_tilesets(context, col_content)
        elif tab == 'GEO_TAG':   self._draw_geo_tag(context, col_content)
        elif tab == 'GENERATOR': self._draw_generator(context, col_content)
        elif tab == 'EXPORT':    self._draw_export(context, col_content)
        elif tab == 'PREVIEW':   self._draw_preview(context, col_content)


# ── Panel 1: Scene Setup ──────────────────────────────────────────────────────

class R3ST_PT_setup(Panel):
    bl_label       = "◈  Scene Setup"
    bl_idname      = "R3ST_PT_setup"
    bl_parent_id   = "R3ST_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'R3ST'

    def draw(self, context):
        layout = self.layout
        s = context.scene.r3st_setup

        box = layout.box()
        bc  = box.column(align=True)
        bc.prop(s, 'project_dir')
        bc.separator(factor=0.8)
        bc.prop(s, 'tile_px')
        bc.separator(factor=0.5)
        res_row = bc.row(align=True)
        res_row.prop(s, 'res_x', text="Res W")
        res_row.prop(s, 'res_y', text="Res H")
        bc.separator(factor=0.5)
        bc.prop(s, 'persp_collection')


# ── Panel 2: Tilesets Generator ───────────────────────────────────────────────

class R3ST_PT_tilesets(Panel):
    bl_label       = "◈  Tilesets Generator"
    bl_idname      = "R3ST_PT_tilesets"
    bl_parent_id   = "R3ST_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'R3ST'
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.r3st_setup
        t = context.scene.r3st_tileset
        _refresh_pkg_cache(s.project_dir)

        gen_box = layout.box()
        gen_box.label(text="Generate Sheets", icon='IMAGE_DATA')
        gc = gen_box.column(align=True)
        gc.prop(t, 'map_name')
        gc.prop(t, 'active_count')
        gc.separator(factor=0.5)
        if s.project_dir:
            gc.label(text=f"→ img/tilesets/R3ST_{t.map_name}_*.png", icon='FILE')
            gc.label(text="→ data/MapInfos.json  +  MapXXX.json",    icon='FILE')
            gc.label(text="Sheets: A5  B  C  D  E",                  icon='TEXTURE')
        else:
            gc.label(text="Set Project Root in Scene Setup first.", icon='INFO')
        gc.separator(factor=0.5)
        gc.operator("r3st.generate_tilesets", icon='IMAGE_DATA')


# ── Panel 3: Geometry Tag ─────────────────────────────────────────────────────

class R3ST_PT_geo_tag(Panel):
    bl_label       = "◈  Geometry Tag"
    bl_idname      = "R3ST_PT_geo_tag"
    bl_parent_id   = "R3ST_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'R3ST'
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.r3st_setup
        t = context.scene.r3st_tileset
        _refresh_pkg_cache(s.project_dir)

        tag_box = layout.box()
        tag_box.label(text="Tag Geometry", icon='MESH_DATA')
        tc = tag_box.column(align=True)
        tc.prop(t, 'tag_map')
        tc.prop(t, 'tag_sheet')
        cr = tc.row(align=True)
        cr.prop(t, 'tag_col')
        cr.prop(t, 'tag_row')
        tc.prop(t, 'tag_render_group')
        tc.separator(factor=0.5)
        er = tc.row(align=True)
        er.prop(t, 'tag_export_type', expand=True)
        tc.prop(t, 'tag_export_group')

        _RG_PROJ = {'0':'ORTHO','1':'ORTHO','2':'PERSP','3':'ORTHO'}

        if t.tag_map != 'NONE':
            tc.separator(factor=0.5)
            grp_preview = f'[{t.tag_export_group}]  ' if t.tag_export_group.strip() else ''
            proj = _RG_PROJ.get(t.tag_render_group, 'ORTHO')
            tc.label(
                text=(f"{grp_preview}R3ST_{t.tag_map}_{t.tag_sheet}"
                      f"  :  {t.tag_col},{t.tag_row}"
                      f"  rg={t.tag_render_group} [{proj}]  →  .{t.tag_export_type.lower()}"),
                icon='CHECKMARK')

        tc.separator(factor=0.8)
        tc.operator("r3st.tag_geometry", icon='OBJECT_DATAMODE')

        obj = context.active_object
        if obj and obj.type == 'MESH':
            read_box = tag_box.box()
            read_box.label(text=f"Active:  {obj.name}", icon='OBJECT_DATA')
            if 'r3st_map' in obj:
                rc = read_box.column(align=True)
                grp  = obj.get('r3st_export_group', '')
                ext  = obj.get('r3st_export_type', 'GLB').lower()
                rg   = str(obj.get('r3st_render_group', 1))
                proj = _RG_PROJ.get(rg, 'ORTHO')
                rc.label(text=(f"  R3ST_{obj['r3st_map']}_{obj['r3st_sheet']}"
                               f"  :  {obj['r3st_col']},{obj['r3st_row']}"
                               f"  rg={rg} [{proj}]"),
                         icon='LINKED')
                rc.label(text=(f"  [{grp}]  →  .{ext}" if grp else f"  (no group)  →  .{ext}"),
                         icon='PACKAGE')
            else:
                read_box.label(text="  No tag", icon='UNLINKED')


# ── Panel 4: Scene Generator ──────────────────────────────────────────────────

class R3ST_PT_room(Panel):
    bl_label       = "◈  Scene Generator"
    bl_idname      = "R3ST_PT_room"
    bl_parent_id   = "R3ST_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'R3ST'
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.r3st_setup
        r = context.scene.r3st_room

        # ── Dimensions ────────────────────────────────────────────────────────
        dim_box = layout.box()
        dim_box.label(text="Dimensions  (= RPG Maker map size)", icon='GRID')
        dc = dim_box.column(align=True)
        dr = dc.row(align=True)
        dr.prop(r, 'room_w', text="W (X)")
        dr.prop(r, 'room_d', text="D (Y)")
        dr.prop(r, 'room_h', text="H (Z)")
        dc.separator(factor=0.5)
        px_w = r.room_w * s.tile_px; px_h = r.room_d * s.tile_px
        dc.label(text=f"Parallax / render:  {px_w} × {px_h} px")
        dc.label(text=f"Cols A–{col_letter(r.room_w-1)}    Rows 0–{r.room_d-1}")

        layout.separator(factor=1.5)

        # ── Actions ───────────────────────────────────────────────────────────
        act_box = layout.box()
        act_box.label(text="Tiles → _R3ST/    Map → img/parallaxes/", icon='INFO')
        act_box.separator(factor=0.3)
        act_box.operator("r3st.generate_tiles",  icon='EXPORT')
        act_box.operator("r3st.build_demo_room", icon='MESH_GRID')
        act_box.separator(factor=0.5)
        act_box.operator("r3st.bake_map",        icon='IMAGE_BACKGROUND')
        act_box.separator(factor=0.5)
        act_box.operator("r3st.prepare_export",  icon='NORMALS_FACE')

        layout.separator(factor=1.5)

        # ── Camera Rig ────────────────────────────────────────────────────────
        rig_box = layout.box()
        rig_box.label(text="Camera Rig", icon='CAMERA_DATA')
        rig_box.operator("r3st.setup_rig", icon='CAMERA_DATA')
        rig_box.separator(factor=0.5)

        pivot   = bpy.data.objects.get(PIVOT_NAME)
        cam_obj = bpy.data.objects.get(CAM_NAME)
        if pivot and cam_obj:
            live_box = rig_box.box()
            live_box.label(text="Live Camera Controls", icon='DRIVER')
            lc = live_box.column(align=True)
            lc.prop(pivot, '["yaw"]',   text="Yaw")
            lc.prop(pivot, '["pitch"]', text="Pitch")
            lc.prop(pivot, '["dist"]',  text="Dist")
            lc.prop(pivot, '["fov"]',   text="FOV")
            lc.prop(pivot, '["roll"]',  text="Roll")

            lc.separator(factor=0.8)

            # Projection toggle — only Perspective and Orthographic
            proj_row = lc.row(align=True)
            proj_row.prop_enum(cam_obj.data, 'type', 'PERSP')
            proj_row.prop_enum(cam_obj.data, 'type', 'ORTHO')
        else:
            rig_box.label(text="Create the rig to see live controls.", icon='INFO')


# ── Panel 6: Export ───────────────────────────────────────────────────────────

class R3ST_PT_export(Panel):
    bl_label       = "◈  Export"
    bl_idname      = "R3ST_PT_export"
    bl_parent_id   = "R3ST_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'R3ST'
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.r3st_setup
        e = context.scene.r3st_export
        _refresh_pkg_cache(s.project_dir)

        # ── Camera Export ─────────────────────────────────────────────────────
        cam_box = layout.box()
        cam_box.label(text="Camera", icon='CAMERA_DATA')
        cc = cam_box.column(align=True)
        cc.prop(e, 'mz3d_default_fov')
        cc.prop(e, 'camera_mode')
        cc.prop(e, 'decimals')
        cc.separator(factor=0.8)
        cc.prop(e, 'target_map')

        pivot = bpy.data.objects.get(PIVOT_NAME)
        if pivot:
            cc.separator(factor=0.5)
            vc = cc.box().column(align=True)
            vc.label(text=f"yaw={round(pivot.get('yaw',0.0),2)}  "
                          f"pitch={round(pivot.get('pitch',45.0),2)}  "
                          f"dist={round(pivot.get('dist',9.0),2)}")
            vc.label(text=f"fov={round(pivot.get('fov',45.0),2)}  "
                          f"roll={round(pivot.get('roll',0.0),2)}")
        else:
            cc.label(text="Run Camera Rig first.", icon='ERROR')

        cc.separator(factor=0.5)
        cc.operator("r3st.export_camera", icon='COPYDOWN')

        layout.separator(factor=2.0)

        # ── Export Level ──────────────────────────────────────────────────────
        lvl_box = layout.box()
        lvl_box.label(text="Export Level", icon='EXPORT')
        lc = lvl_box.column(align=True)
        if s.project_dir:
            # Always-present room export
            room_obj = bpy.data.objects.get(ROOM_NAME)
            obj_name = room_obj.name if room_obj else ROOM_NAME
            lc.label(text=f"  {obj_name}.obj  (room — always)", icon='FILE')

            # Live group summary from tagged scene objects
            groups = {}
            for obj in bpy.data.objects:
                if obj.type != 'MESH' or obj.name == ROOM_NAME:
                    continue
                grp = obj.get('r3st_export_group', '').strip()
                ext = obj.get('r3st_export_type', 'GLB')
                if grp:
                    groups.setdefault((grp, ext), 0)
                    groups[(grp, ext)] += 1
            if groups:
                lc.separator(factor=0.3)
                for (grp, ext), count in sorted(groups.items()):
                    lc.label(text=f"  {grp}.{ext.lower()}  ({count} object(s))", icon='FILE')
        else:
            lc.label(text="Set Project Root in Scene Setup first.", icon='INFO')
        lc.separator(factor=0.5)
        lc.operator("r3st.export_level", icon='MESH_DATA')


# ══════════════════════════════════════════════════════════════════════════════
# PREVIEW  —  two-pass single-scene render, Python composite
# ══════════════════════════════════════════════════════════════════════════════
#
# Architecture — NO separate scenes are created (creating scenes via
# bpy.data.scenes.new() in Blender 5.0.1 causes a GPU_matrix_ortho_set crash
# in the draw loop because the new scenes are never given a proper GPU context).
#
# Instead, "Render Preview" works entirely in the current scene:
#   Pass 1 — camera → ORTHO,  Characters collection excluded  → BG pixels
#   Pass 2 — camera → PERSP,  only Characters collection visible → char pixels
#   Python Alpha Over → R3ST_Preview image → Image Editor
#
# "Setup Preview" just validates pre-conditions; no scene creation.

# ── Helper ────────────────────────────────────────────────────────────────────

def _find_layer_col(view_layer, col_name):
    """Return the LayerCollection for col_name, or None."""
    def _walk(lc):
        if lc.collection.name == col_name:
            return lc
        for child in lc.children:
            r = _walk(child)
            if r:
                return r
        return None
    return _walk(view_layer.layer_collection)


# ── Operator: Setup Preview (validation only) ─────────────────────────────────

class R3ST_OT_setup_preview(Operator):
    bl_idname      = "r3st.setup_preview"
    bl_label       = "Check Preview Setup"
    bl_description = (
        "Validate that the camera rig and Persp Collection exist so that "
        "'Render Preview' can run. No scenes are created."
    )

    def execute(self, context):
        s              = context.scene.r3st_setup
        persp_col_name = s.persp_collection.strip() or 'Characters'
        ok = True

        if not bpy.data.objects.get(PIVOT_NAME):
            self.report({'ERROR'},
                        "Camera rig not found — run 'Create / Rebuild Rig' first.")
            ok = False

        if not bpy.data.objects.get(CAM_NAME):
            self.report({'ERROR'},
                        f"'{CAM_NAME}' not found — run 'Create / Rebuild Rig' first.")
            ok = False

        if not bpy.data.collections.get(persp_col_name):
            self.report({'ERROR'},
                        f"Collection '{persp_col_name}' not found — "
                        "create it in the Outliner and move your characters into it.")
            ok = False

        if ok:
            self.report({'INFO'},
                        f"Preview ready.  Persp collection: '{persp_col_name}'.  "
                        "Hit 'Render Preview' to render.")
        return {'FINISHED'} if ok else {'CANCELLED'}


# ── Operator: Render Preview ──────────────────────────────────────────────────

class R3ST_OT_render_preview(Operator):
    bl_idname      = "r3st.render_preview"
    bl_label       = "Render Preview"
    bl_description = (
        "Two-pass render in the current scene: "
        "Pass 1 = ortho camera (BG, characters hidden), "
        "Pass 2 = persp camera (characters only, transparent BG). "
        "Composites both passes in Python → 'R3ST_Preview' image."
    )

    # When True the operator is called from _r3st_continuous_timer and may
    # skip unchanged passes via dirty flags.  When False (manual button press)
    # both passes are always re-rendered so stale caches never surface.
    is_continuous: BoolProperty(default=False, options={'SKIP_SAVE'})

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _load_pixels(filepath, w, h):
        """
        Load a rendered TGA into a float32 (h, w, 4) array.

        Fast path: TARGA_RAW memmap — avoids Blender's image pipeline entirely.

        Key subtleties:
        - Blender writes a TGA 2.0 file: 18-byte header + pixel data + extension
          area (495 bytes) + footer (26 bytes).  np.memmap without shape= maps
          the entire file tail; reshape fails on the 521 trailing bytes.
          Fix: shape=(pixel_count,) pins the map to exactly the pixel bytes.
        - Standard TGA is bottom-up (row 0 = bottom), matching Blender's
          foreach_set/foreach_get convention → no flip for standard TGA.
          Flip only when the TGA signals top-down origin (header[17] bit 5).
        - Blender TARGA_RAW channel order is BGRA → reorder to RGBA.

        Fallback: bpy.data.images.load() for any format Blender actually wrote.
        """
        import numpy as np, os
        TGA_HEADER = 18

        # ── Locate file (write_still sometimes appends frame number) ─────────
        actual_path = filepath
        if not os.path.exists(filepath):
            base, ext = os.path.splitext(filepath)
            for suffix in ('0001', '001', '01', '1'):
                candidate = f"{base}{suffix}{ext}"
                if os.path.exists(candidate):
                    actual_path = candidate
                    break
            else:
                return np.zeros((h, w, 4), dtype=np.float32)

        # ── Fast path: TARGA_RAW memmap ───────────────────────────────────────
        try:
            with open(actual_path, 'rb') as f:
                header = bytearray(f.read(TGA_HEADER))

            if header[2] == 2:  # uncompressed true-colour TGA (TARGA_RAW)
                id_length = header[0]
                bpp       = header[16]
                channels  = bpp // 8
                top_down  = bool(header[17] & 0x20)
                offset    = TGA_HEADER + id_length  # skip optional ID field

                if channels in (3, 4):
                    pixel_count = h * w * channels
                    # shape= limits the mapping to exactly the pixel bytes,
                    # ignoring the TGA 2.0 extension area + footer that Blender
                    # appends after the pixel data (otherwise reshape fails).
                    mm  = np.memmap(actual_path, dtype=np.uint8, mode='r',
                                    offset=offset, shape=(pixel_count,))
                    raw = mm.reshape((h, w, channels))

                    # Standard TGA bottom-up == Blender foreach_set bottom-up.
                    # Flip only when the file signals top-down origin.
                    if top_down:
                        raw = raw[::-1]

                    if channels == 4:
                        rgba = raw[:, :, [2, 1, 0, 3]].astype(np.float32) / 255.0
                    else:
                        rgb  = raw[:, :, [2, 1, 0]].astype(np.float32) / 255.0
                        rgba = np.ones((h, w, 4), dtype=np.float32)
                        rgba[..., :3] = rgb

                    # render.opengl captures the viewport display buffer —
                    # the View Transform (AgX etc.) is already baked into the
                    # uint8 bytes.  The Image Editor will apply the View
                    # Transform again when displaying the float_buffer result
                    # image, which causes double-processing → overblown.
                    # Fix: linearise the RGB channels (sRGB → linear) before
                    # storing, so the Standard View Transform round-trips them
                    # back to approximately the original display values.
                    # (This matches what bpy.data.images.load with 'sRGB'
                    # colorspace does automatically via foreach_get.)
                    rgb = rgba[..., :3]
                    rgba[..., :3] = np.where(
                        rgb <= 0.04045,
                        rgb / 12.92,
                        ((rgb + 0.055) / 1.055) ** 2.4,
                    )
                    return rgba
        except Exception:
            pass  # fall through to bpy loader

        # ── Fallback: bpy.data.images.load (handles any format) ──────────────
        try:
            import bpy, traceback
            tmp_name = '__r3st_tmp__'
            existing = bpy.data.images.get(tmp_name)
            if existing:
                bpy.data.images.remove(existing)
            img = bpy.data.images.load(actual_path)
            img.name = tmp_name
            # 'sRGB' tells Blender to linearise the stored bytes via
            # foreach_get, matching the sRGB→linear conversion applied
            # manually in the memmap fast path above.
            img.colorspace_settings.name = 'sRGB'
            arr = np.zeros(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(arr)
            bpy.data.images.remove(img)
            return arr.reshape((h, w, 4))
        except Exception:
            return np.zeros((h, w, 4), dtype=np.float32)

    @staticmethod
    def _col_has_renderable(collection, vl):
        """
        Return True if the collection contains at least one object that is
        neither render-hidden nor viewport-hidden and is not excluded from
        the active view layer.  Used to skip pass 2 when the persp collection
        is effectively empty, saving a full render.opengl() call.
        """
        # Find the LayerCollection for this collection
        def _find_lc(root_lc, target):
            if root_lc.collection == target:
                return root_lc
            for child in root_lc.children:
                found = _find_lc(child, target)
                if found:
                    return found
            return None

        lc = _find_lc(vl.layer_collection, collection)
        if lc is None or lc.exclude:
            return False
        for obj in collection.all_objects:
            if not obj.hide_render and not obj.hide_viewport and not obj.hide_get():
                return True
        return False

    @staticmethod
    def _walk_layer_cols(root_lc):
        """Yield every LayerCollection in the tree rooted at root_lc."""
        yield root_lc
        for child in root_lc.children:
            yield from R3ST_OT_render_preview._walk_layer_cols(child)

    @staticmethod
    def _save_layer_state(vl):
        """
        Recursively snapshot ALL layer collections (not just top-level):
          { col_name: (lc.exclude, lc.hide_viewport) }
        We read both flags to determine original visibility and restore both
        after the passes to prevent any collection from staying unhidden.
        """
        result = {}
        for lc in R3ST_OT_render_preview._walk_layer_cols(vl.layer_collection):
            result[lc.collection.name] = (lc.exclude, lc.hide_viewport)
        return result

    @staticmethod
    def _restore_layer_state(vl, snapshot):
        """
        Recursively restore lc.exclude AND lc.hide_viewport on every collection.
        Blender resets hide_viewport when exclude is toggled so we must
        write both back or viewport-hidden collections will reappear.
        """
        for lc in R3ST_OT_render_preview._walk_layer_cols(vl.layer_collection):
            state = snapshot.get(lc.collection.name)
            if state:
                lc.exclude       = state[0]
                lc.hide_viewport = state[1]

    @staticmethod
    def _is_hidden(snapshot, col_name):
        """True if the collection was excluded or viewport-hidden."""
        state = snapshot.get(col_name)
        if state is None:
            return False
        excl, hide_vp = state
        return excl or hide_vp


    # ── Render helper ─────────────────────────────────────────────────────────

    @staticmethod
    def _do_render(scene, filepath):
        """
        Render the current scene to *filepath* as fast as possible.

        Strategy — try viewport OpenGL render first (EEVEE, near-instant).

        The viewport is temporarily forced into camera view so the output
        always shows R3ST_Camera with full material shading (textures etc.).

        Per Blender's internal C code and the Stored Views bug #99751:
        setting view_perspective = 'CAMERA' does NOT directly overwrite
        view_location/rotation/distance, but render.opengl() does modify
        them as a side effect of camera-locking the viewport for the draw
        pass.  We therefore snapshot ALL FOUR view-state properties and
        restore them in the correct order (distance/location/rotation first,
        perspective last) so the user's free-orbit position is preserved.
        """
        scene.render.filepath = filepath
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                space = area.spaces.active
                r3d   = space.region_3d

                # Full view-state + overlay snapshot.
                # .copy() is essential for mathutils Vector/Quaternion (live refs).
                orig_perspective = r3d.view_perspective
                orig_location    = r3d.view_location.copy()
                orig_rotation    = r3d.view_rotation.copy()
                orig_distance    = r3d.view_distance
                orig_overlays    = space.overlay.show_overlays

                r3d.view_perspective         = 'CAMERA'
                # Disable overlays so the floor grid, wire edges, etc. are not
                # baked into the render output.
                space.overlay.show_overlays  = False
                try:
                    for region in area.regions:
                        if region.type != 'WINDOW':
                            continue
                        with bpy.context.temp_override(
                                window=window, area=area, region=region):
                            bpy.ops.render.opengl(write_still=True)
                        return      # done — opengl render succeeded
                finally:
                    # Restore spatial state first, perspective last.
                    r3d.view_distance            = orig_distance
                    r3d.view_location            = orig_location
                    r3d.view_rotation            = orig_rotation
                    r3d.view_perspective         = orig_perspective
                    space.overlay.show_overlays  = orig_overlays
        # Fallback: full engine render (slower, no viewport state to worry about)
        bpy.ops.render.render(write_still=True)

    # ── Execute ───────────────────────────────────────────────────────────────

    def execute(self, context):
        import numpy as np

        scene          = context.scene
        s              = scene.r3st_setup
        persp_col_name = s.persp_collection.strip() or 'Characters'

        cam_obj = bpy.data.objects.get(CAM_NAME)
        if not cam_obj:
            self.report({'ERROR'},
                        f"'{CAM_NAME}' not found — run 'Create / Rebuild Rig' first.")
            return {'CANCELLED'}

        persp_col = bpy.data.collections.get(persp_col_name)
        if not persp_col:
            self.report({'ERROR'},
                        f"Collection '{persp_col_name}' not found — "
                        "create it and move your characters into it.")
            return {'CANCELLED'}

        cam_data = cam_obj.data
        vl       = scene.view_layers[0]

        # ── Resolution: apply preview percentage to render resolution ─────────
        orig_pct = scene.render.resolution_percentage
        scene.render.resolution_percentage = int(s.preview_pct)
        w = int(scene.render.resolution_x * int(s.preview_pct) / 100)
        h = int(scene.render.resolution_y * int(s.preview_pct) / 100)

        # ── Which passes to render? ───────────────────────────────────────────
        # Manual button press: always re-render both (user expects a fresh result).
        # Continuous render: respect per-pass dirty flags so unchanged geometry
        # reuses its cached pass array without a re-render.
        st = _r3st_continuous
        if self.is_continuous:
            run_pass1 = st.get('dirty_pass1', True)
            run_pass2 = st.get('dirty_pass2', True)
        else:
            run_pass1 = True
            run_pass2 = True
        # Clear flags — the depsgraph handler will re-set them on the next change.
        st['dirty_pass1'] = False
        st['dirty_pass2'] = False

        # ── Cache dimension guard ─────────────────────────────────────────────
        # If the resolution changed (e.g. Full → Half) the cached arrays are
        # the wrong size; using them would cause foreach_set to crash.
        # Detect the mismatch and force both passes to re-render.
        cached_bg = _r3st_pass_cache.get('bg')
        if cached_bg is not None and cached_bg.shape[:2] != (h, w):
            _r3st_pass_cache.clear()
            run_pass1 = True
            run_pass2 = True

        # ── Does the persp collection actually contain renderable objects? ─────
        has_chars = self._col_has_renderable(persp_col, vl)

        # Save state we'll restore after both passes
        orig_cam_type    = cam_data.type
        orig_transparent = scene.render.film_transparent
        orig_filepath    = scene.render.filepath
        orig_format      = scene.render.image_settings.file_format
        orig_color_mode  = scene.render.image_settings.color_mode
        orig_scene_cam   = scene.camera          # may differ from R3ST_Camera
        layer_snapshot   = self._save_layer_state(vl)

        # Pin the scene camera to R3ST_Camera so both render passes always
        # use our rig, regardless of what scene.camera was before.
        scene.camera = cam_obj
        # Snapshot the LOCAL eye-icon hide state (obj.hide_get()) — this is the
        # per-view-layer ObjectBase flag set by H key and the Outliner eye icon.
        # This is completely separate from obj.hide_viewport (the global monitor
        # icon). The render depsgraph evaluation resets hide_get() state, so we
        # must capture and restore it explicitly around the render calls.
        obj_hide_snapshot = {obj.name: obj.hide_get() for obj in scene.objects}

        import tempfile, os
        tmp_dir = tempfile.gettempdir()

        try:
            # TARGA_RAW — uncompressed, no codec, read back via numpy memmap.
            scene.render.image_settings.file_format     = 'TARGA_RAW'
            scene.render.image_settings.color_mode      = 'RGBA'

            # ── Pass 1: ortho BG ──────────────────────────────────────────────
            if run_pass1:
                cam_data.type                 = 'ORTHO'
                scene.render.film_transparent = False
                for lc in vl.layer_collection.children:
                    name = lc.collection.name
                    lc.exclude = (name == persp_col_name) or \
                                 self._is_hidden(layer_snapshot, name)
                tmp_bg = os.path.join(tmp_dir, 'r3st_bg.tga')
                self._do_render(scene, tmp_bg)
                _r3st_pass_cache['bg'] = self._load_pixels(tmp_bg, w, h)

            bg_px = _r3st_pass_cache.get('bg')

            # ── Pass 2: persp characters ──────────────────────────────────────
            # Skip entirely if the collection is empty — no render needed.
            if run_pass2 and has_chars:
                cam_data.type                 = 'PERSP'
                scene.render.film_transparent = True
                chars_was_hidden = self._is_hidden(layer_snapshot, persp_col_name)
                for lc in vl.layer_collection.children:
                    name = lc.collection.name
                    lc.exclude = (name != persp_col_name) or chars_was_hidden
                tmp_chars = os.path.join(tmp_dir, 'r3st_chars.tga')
                self._do_render(scene, tmp_chars)
                _r3st_pass_cache['chars'] = self._load_pixels(tmp_chars, w, h)

            char_px = _r3st_pass_cache.get('chars')

        finally:
            cam_data.type                               = orig_cam_type
            scene.render.film_transparent               = orig_transparent
            scene.render.filepath                       = orig_filepath
            scene.render.image_settings.file_format     = orig_format
            scene.render.image_settings.color_mode      = orig_color_mode
            scene.render.resolution_percentage          = orig_pct
            scene.camera                                = orig_scene_cam
            self._restore_layer_state(vl, layer_snapshot)
            # Restore the local eye-icon hide state via hide_set() — must match
            # what we captured with hide_get(). Do this AFTER collection restore
            # since the depsgraph update triggered by lc.exclude changes can
            # reset per-object hide_set state too.
            for obj in scene.objects:
                if obj.name in obj_hide_snapshot:
                    obj.hide_set(obj_hide_snapshot[obj.name])

        # ── Alpha Over composite (persp chars on top of ortho BG) ────────────
        if bg_px is None:
            return {'CANCELLED'}   # no cached BG yet (first frame, both skipped)

        if char_px is not None and has_chars:
            a      = char_px[..., 3:4]
            result = char_px * a + bg_px * (1.0 - a)
            result[..., 3] = 1.0
        else:
            result = bg_px.copy()
            result[..., 3] = 1.0

        # ── Write composite to R3ST_Preview image data-block ─────────────────
        result_name = 'R3ST_Preview'
        result_img  = bpy.data.images.get(result_name)
        if result_img is None or list(result_img.size) != [w, h]:
            if result_img:
                bpy.data.images.remove(result_img)
            result_img = bpy.data.images.new(result_name, width=w, height=h,
                                             alpha=True, float_buffer=True)
        # result arrays are already in linear space (sRGB→linear applied in
        # _load_pixels).  The float_buffer image keeps its default Linear
        # colorspace so the Standard View Transform in the Image Editor
        # round-trips them back to the original display-space appearance.
        result_img.pixels.foreach_set(result.ravel())
        result_img.update()

        # ── Show in Image Editor (deferred — avoids any mid-draw GPU state) ───
        def _show_result():
            img = bpy.data.images.get(result_name)
            if img:
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == 'IMAGE_EDITOR':
                            area.spaces.active.image = img
                            break
            return None  # run once only

        bpy.app.timers.register(_show_result, first_interval=0.1)

        self.report({'INFO'},
                    f"Preview done → '{result_name}' in Image Editor  "
                    f"({w}×{h} px, ortho BG + persp chars).")
        return {'FINISHED'}


# ── Continuous render state ───────────────────────────────────────────────────
#
# We keep a plain dict (not a bpy.props) so it survives operator cancels and
# is accessible from both the operator and the timer / depsgraph handler.

# Cached numpy arrays for each pass — lets us skip re-rendering the unchanged
# pass when only BG or only character geometry changes.
_r3st_pass_cache = {}   # keys: 'bg', 'chars' → float32 (h,w,4) arrays

_r3st_continuous = {
    'active':      False,
    'dirty':       False,  # True = at least one pass needs a re-render
    'dirty_pass1': True,   # BG (ortho) pass needs re-render
    'dirty_pass2': True,   # Chars (persp) pass needs re-render
    'last_time':   0.0,
    'debounce':    0.6,    # minimum seconds between auto-renders
    'rendering':   False,  # True while a render pass is executing
}


_R3ST_RIG_NAMES = frozenset((PIVOT_NAME, ARM_NAME, CAM_NAME))


def _r3st_depsgraph_handler(scene, depsgraph):
    """
    Set per-pass dirty flags when actual scene content changes.

    Orbiting / navigating the viewport moves the R3ST camera rig via its
    drivers, which fires depsgraph_update_post on every mouse move.  We
    deliberately skip updates that are ONLY about the rig objects so that
    pure navigation never triggers a re-render.

    Per-pass tracking: if the changed object lives in the persp collection
    we only dirty pass 2 (chars); otherwise we dirty pass 1 (BG).  When
    both flags are set the timer re-renders both passes; when only one is
    set the other uses its cached numpy array from the previous cycle.
    """
    if not _r3st_continuous['active'] or _r3st_continuous['rendering']:
        return
    st = _r3st_continuous
    # Name of the persp collection — read live from scene props.
    try:
        persp_col_name = (scene.r3st_setup.persp_collection.strip() or 'Characters')
        persp_col      = bpy.data.collections.get(persp_col_name)
    except Exception:
        persp_col = None

    for update in depsgraph.updates:
        id_data = update.id
        # Skip the R3ST camera rig — drivers fire on every navigation action.
        if isinstance(id_data, bpy.types.Object) and id_data.name in _R3ST_RIG_NAMES:
            continue
        # Skip Scene-level — fires on our own render-state mutations.
        if isinstance(id_data, bpy.types.Scene):
            continue
        if not isinstance(id_data, (bpy.types.Object, bpy.types.Mesh,
                                    bpy.types.Material)):
            continue

        # Determine which pass to dirty based on collection membership.
        if isinstance(id_data, bpy.types.Object) and persp_col is not None:
            if id_data.name in persp_col.all_objects:
                st['dirty_pass2'] = True
            else:
                st['dirty_pass1'] = True
        else:
            # Mesh / Material — can't cheaply determine pass; dirty both.
            st['dirty_pass1'] = True
            st['dirty_pass2'] = True

        st['dirty'] = True
        return


def _r3st_continuous_timer():
    """
    Recurring timer (0.3 s).  Triggers a preview render when the scene has
    changed and the debounce interval has elapsed.
    Returns None to unregister itself once continuous mode is stopped.
    """
    import time
    st = _r3st_continuous
    if not st['active']:
        return None     # stops the timer
    if st['dirty'] and (time.time() - st['last_time']) >= st['debounce']:
        st['dirty']     = False
        st['last_time'] = time.time()
        st['rendering'] = True
        try:
            bpy.ops.r3st.render_preview(is_continuous=True)
        except Exception:
            pass
        finally:
            st['rendering'] = False
    return 0.3          # reschedule


# ── Operator: Continuous Render Toggle ───────────────────────────────────────

class R3ST_OT_continuous_render(Operator):
    bl_idname      = "r3st.continuous_render"
    bl_label       = "Continuous Render"
    bl_description = (
        "Toggle live preview: re-renders automatically after every scene "
        "change (uses viewport OpenGL for speed). Click again to stop."
    )

    def execute(self, context):
        import time
        st = _r3st_continuous

        if st['active']:
            # ── Stop ──────────────────────────────────────────────────────────
            st['active'] = False
            if _r3st_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
                bpy.app.handlers.depsgraph_update_post.remove(
                    _r3st_depsgraph_handler)
            self.report({'INFO'}, "R3ST: continuous render stopped.")
        else:
            # ── Start ─────────────────────────────────────────────────────────
            st['active']      = True
            st['dirty']       = False
            st['dirty_pass1'] = True   # force full render on first activation
            st['dirty_pass2'] = True
            st['last_time']   = time.time()
            _r3st_pass_cache.clear()   # discard stale cached passes
            if _r3st_depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
                bpy.app.handlers.depsgraph_update_post.append(
                    _r3st_depsgraph_handler)
            if not bpy.app.timers.is_registered(_r3st_continuous_timer):
                bpy.app.timers.register(_r3st_continuous_timer,
                                        first_interval=0.3)
            self.report({'INFO'}, "R3ST: continuous render started.")

        # Force panel redraw so the button label/icon updates immediately
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

        return {'FINISHED'}


# ── Panel 5: Preview ──────────────────────────────────────────────────────────

class R3ST_PT_preview(Panel):
    bl_label       = "◈  Preview"
    bl_idname      = "R3ST_PT_preview"
    bl_parent_id   = "R3ST_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'R3ST'
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s      = context.scene.r3st_setup

        info_box = layout.box()
        info_box.label(text="Ortho BG + Persp Characters", icon='CAMERA_STEREO')
        ic = info_box.column(align=True)
        ic.label(text="Pass 1 — ortho cam, chars hidden  → BG",    icon='HIDE_OFF')
        ic.label(text="Pass 2 — persp cam, chars only    → Chars", icon='HIDE_ON')
        ic.label(text="Python Alpha Over → R3ST_Preview image",     icon='IMAGE_DATA')

        layout.separator(factor=1.2)

        cfg_box = layout.box()
        cfg_box.label(text="Configuration", icon='SETTINGS')
        cfg_box.prop(s, 'persp_collection')

        # Live status
        rig_ok = bool(bpy.data.objects.get(CAM_NAME))
        col_ok = bool(bpy.data.collections.get(s.persp_collection.strip() or 'Characters'))

        sc_box = cfg_box.box().column(align=True)
        sc_box.label(
            text=f"Rig:               {'found' if rig_ok else 'missing — create rig first'}",
            icon='CHECKMARK' if rig_ok else 'ERROR')
        sc_box.label(
            text=f"Persp collection:  {'found' if col_ok else 'missing — create in Outliner'}",
            icon='CHECKMARK' if col_ok else 'ERROR')

        layout.separator(factor=1.2)

        act_box = layout.box()

        ready = rig_ok and col_ok

        # ── Single render ──────────────────────────────────────────────────
        row = act_box.row()
        row.enabled = ready
        row.operator("r3st.render_preview", text="Render Preview",
                     icon='RENDER_STILL')

        # ── Continuous render toggle ───────────────────────────────────────
        cont_active = _r3st_continuous['active']
        row2 = act_box.row()
        row2.enabled = ready or cont_active   # allow stopping even if rig missing
        row2.alert   = cont_active            # red tint while running
        row2.operator(
            "r3st.continuous_render",
            text="Stop Continuous" if cont_active else "Continuous Render",
            icon='PAUSE'          if cont_active else 'REC',
        )


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

_classes = (
    R3ST_SetupProps,
    R3ST_RoomProps,
    R3ST_TilesetProps,
    R3ST_ExportProps,
    R3ST_OT_setup_rig,
    R3ST_OT_generate_tiles,
    R3ST_OT_build_demo_room,
    R3ST_OT_bake_map,
    R3ST_OT_prepare_export,
    R3ST_OT_generate_tilesets,
    R3ST_OT_tag_geometry,
    R3ST_OT_export_camera,
    R3ST_OT_export_level,
    R3ST_OT_render_preview,
    R3ST_OT_continuous_render,
    R3ST_PT_main,
    # Old subpanels kept for reference but no longer registered —
    # their content is now drawn directly by R3ST_PT_main._draw_*() methods.
    # R3ST_PT_setup, R3ST_PT_tilesets, R3ST_PT_room,
    # R3ST_PT_geo_tag, R3ST_PT_export, R3ST_PT_preview,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.r3st_setup   = bpy.props.PointerProperty(type=R3ST_SetupProps)
    bpy.types.Scene.r3st_room    = bpy.props.PointerProperty(type=R3ST_RoomProps)
    bpy.types.Scene.r3st_tileset = bpy.props.PointerProperty(type=R3ST_TilesetProps)
    bpy.types.Scene.r3st_export  = bpy.props.PointerProperty(type=R3ST_ExportProps)


def unregister():
    # Stop continuous render cleanly before unregistering
    _r3st_continuous['active'] = False
    if _r3st_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_r3st_depsgraph_handler)

    del bpy.types.Scene.r3st_setup
    del bpy.types.Scene.r3st_room
    del bpy.types.Scene.r3st_tileset
    del bpy.types.Scene.r3st_export
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
