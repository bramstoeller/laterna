"""Editing and saving the placement calibration in config.yaml.

A selection of None means the global calibration: image_offset (position),
rotation, and scale_mm_per_px (the global scale — how many stage mm one
pixel covers). An object id selects that object's origin/scale/rotation. The corner
functions edit the inner corners of an object's frame (`frame.inner`).
Used by the global aligner (align_global.py, global only) and the shape
calibrator (configure.py, per object).
"""

import copy
import math

import numpy as np
import yaml

from . import render


def get_object(cfg, oid):
    return next(o for o in cfg['objects'] if o['id'] == oid)


def snapshot(cfg):
    """Startup values per selection, for reset."""
    snap = {
        None: (
            list(cfg.get('image_offset', [0.0, 0.0])),
            float(cfg['scale_mm_per_px']),
            float(cfg.get('rotation', 0.0)),
        ),
        'keystone': keystone_offsets(cfg),
    }
    for o in cfg['objects']:
        snap[o['id']] = (
            list(o['origin']),
            float(o.get('scale', 1.0)),
            float(o.get('rotation', 0.0)),
        )
    return snap


def keystone_offsets(cfg):
    """The four keystone corner offsets [[dx, dy], ...] (projector px, y
    down; order render.KEYSTONE_CORNERS), zeros when there is none."""
    return [list(map(float, c)) for c in cfg.get('keystone') or [[0.0, 0.0]] * 4]


def move_keystone(cfg, corner, dx, dy):
    """Move keystone corner `corner` (0..3) by (dx, dy) projector px; the
    key disappears from the config again when all offsets are back at 0."""
    offsets = keystone_offsets(cfg)
    offsets[corner] = [offsets[corner][0] + dx, offsets[corner][1] + dy]
    set_keystone(cfg, offsets)


def set_keystone(cfg, offsets):
    if any(v for c in offsets for v in c):
        cfg['keystone'] = [list(c) for c in offsets]
    else:
        cfg.pop('keystone', None)


def move(cfg, selected, dx, dy):
    """Move the selection by (dx, dy) world mm, y up."""
    if selected is None:
        # image_offset is the world position of the image bottom centre, so
        # moving the picture right/up means decreasing the offset; not
        # rounded, so whole-pixel steps stay exact whatever the scale
        offset = cfg.setdefault('image_offset', [0.0, 0.0])
        offset[0] = offset[0] - dx
        offset[1] = offset[1] - dy
    else:
        origin = get_object(cfg, selected)['origin']
        origin[0] = round(origin[0] + dx, 1)
        origin[1] = round(origin[1] + dy, 1)


def object_size_mm(cfg, obj):
    """Largest dimension of the object's screen shape (unscaled, mm)."""
    pts = np.vstack([p['points'] for p in render.object_shape(cfg, obj)['polygons']])
    return float((pts.max(axis=0) - pts.min(axis=0)).max())


def scale_by(cfg, selected, delta):
    """Resize the selection.

    Globally `delta` is a fraction and edits scale_mm_per_px (a larger
    projection means fewer mm per pixel). Per object `delta` is in mm: the
    shape grows or shrinks by exactly that much in its largest dimension.
    """
    if selected is None:
        mmpp = float(cfg['scale_mm_per_px'])
        cfg['scale_mm_per_px'] = round(mmpp / (1.0 + delta), 4)
    else:
        obj = get_object(cfg, selected)
        step = delta / object_size_mm(cfg, obj)
        obj['scale'] = round(max(0.001, float(obj.get('scale', 1.0)) + step), 6)


def rotate_by(cfg, selected, delta):
    target = cfg if selected is None else get_object(cfg, selected)
    target['rotation'] = round(float(target.get('rotation', 0.0)) + delta, 2)


def reset(cfg, selected, snap):
    position, scale, rotation = snap[selected]
    if selected is None:
        cfg['image_offset'] = list(position)
        cfg['scale_mm_per_px'] = scale
        cfg['rotation'] = rotation
        set_keystone(cfg, snap['keystone'])
    else:
        obj = get_object(cfg, selected)
        obj['origin'] = list(position)
        obj['scale'] = scale
        obj['rotation'] = rotation


# --- corners ----------------------------------------------------------------


def corner_count(cfg, obj):
    return len(obj['frame']['inner'])


def corner_snapshot(cfg):
    """Startup inner corners per object id, for reset."""
    return {o['id']: copy.deepcopy(o['frame']['inner']) for o in cfg['objects']}


def move_corner(cfg, selected, index, dx, dy):
    """Move inner corner `index` of the selected object by (dx, dy) world
    mm, y up. The corners are local mm — before the object's scale and
    rotation and the global rotation — so the world step is turned back
    into a local one."""
    obj = get_object(cfg, selected)
    a = -math.radians(float(obj.get('rotation', 0.0)) + float(cfg.get('rotation', 0.0)))
    scale = float(obj.get('scale', 1.0))
    lx = (dx * math.cos(a) - dy * math.sin(a)) / scale
    ly = (dx * math.sin(a) + dy * math.cos(a)) / scale
    x, y = obj['frame']['inner'][index]
    obj['frame']['inner'][index] = [round(x + lx, 1), round(y + ly, 1)]


def reset_corners(cfg, selected, snap, index=None):
    """Put one corner (or all of them, index None) back to the startup
    values."""
    inner = get_object(cfg, selected)['frame']['inner']
    if index is None:
        inner[:] = copy.deepcopy(snap[selected])
    else:
        inner[index] = list(snap[selected][index])


def save_config(cfg, path):
    data = {k: v for k, v in cfg.items() if not str(k).startswith('_')}
    with open(path, 'w') as f:
        yaml.dump(data, f, sort_keys=False, default_flow_style=None, width=100, allow_unicode=True)
