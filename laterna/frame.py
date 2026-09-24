"""Frames built from planks, defined by the inner corners of the wood.

An object's `frame:` in config.yaml:

    frame:
      border: 94                 # plank width, mm
      inner: [[x, y], ...]       # corners of the picture area, local mm, y up

The inner corners are where the wood meets the cloth: the edge that has
to be exact, and the one you can see (wood against a light screen). The
rest follows from the plank width:

- the picture area (canvas) is the inner polygon itself;
- the wood's outer contour is the inner polygon `border` outwards with
  mitred corners;
- the projected molding is that contour, except that a corner with a
  small turn (below ROUND_BELOW_DEG, the knuckles of an arch) is rounded:
  a circular fillet tangent to both planks at half the shorter plank from
  the corner. On a regular arch these fillets join into one circle that
  touches every piece in its middle; the wood beyond the fillet is painted
  black on the set;
- the molding is the band `border` wide inside the projected contour,
  cut away where the canvas is;
- the round canvas is the projected contour `border` inwards: the sight
  edge of the fictional round frame, which the frame's shadow follows (at
  a rounded corner it cuts across the real inner corner).

Local (0, 0) is put on the stage by the object's origin (its scale and
rotation pivot there); the frames stand with the middle of the wood's
bottom edge on it. build() returns the geometry render.py draws:
{'polygons': [{'points', 'color'}] (the screen: filled, masks the
pictures), 'strokes': [{'points', 'width', 'color'}] (the molding: a
stroke centred on its path, shaded with the relief profile), 'canvas':
[points] (the picture area, cut out of the molding), 'round_canvas':
[points] (the fictional round sight edge, for the shadow), 'corners' (the inner
corners) and 'wood' (the mitred outer polygon) for the shape calibration}.
"""

import math

import numpy as np

ROUND_BELOW_DEG = 45.0  # a corner turning less than this is a fillet
STEP_MM = 2.0  # chord length the fillets are flattened to
COLOR = (210, 170, 85)  # the molding as drawn (shaded by render.py)
FILL = (55, 46, 32)  # the screen where a stage maps nothing


def offset_polyline(points, offset, closed):
    """Parallel curve of a polyline at signed distance `offset` (positive =
    to the left of the direction of travel), with mitred corners like an SVG
    stroke (miter limit 4, beyond that bevelled). Returns (n, 2) points; for
    a closed path the input's repeated last point is dropped."""
    p = np.asarray(points, np.float64)
    if closed and len(p) > 1 and np.allclose(p[0], p[-1]):
        p = p[:-1]
    if closed:
        d = np.roll(p, -1, axis=0) - p  # segment i: p[i] -> p[i+1]
    else:
        d = p[1:] - p[:-1]
    length = np.hypot(d[:, 0], d[:, 1])
    keep = length > 1e-9
    if not keep.all():  # drop zero-length segments
        p = p[np.append(keep, True)] if not closed else p[keep]
        return offset_polyline(p, offset, closed) if len(p) > 1 else p
    d /= length[:, None]
    normal = np.stack([-d[:, 1], d[:, 0]], axis=1)  # left normals per segment
    if closed:
        n_prev = np.roll(normal, 1, axis=0)  # segment arriving at vertex i
        n_next = normal  # segment leaving vertex i
    else:
        n_prev = np.vstack([normal[:1], normal])
        n_next = np.vstack([normal, normal[-1:]])
    bisector = n_prev + n_next
    denom = 1.0 + np.sum(n_prev * n_next, axis=1)  # = |bisector|^2 / 2
    with np.errstate(divide='ignore', invalid='ignore'):
        mitre = bisector / denom[:, None]  # exact mitre offset direction
    too_long = ~np.isfinite(mitre).all(axis=1) | (np.hypot(mitre[:, 0], mitre[:, 1]) > 4.0)
    if too_long.any():  # miter limit: bevel instead
        b = bisector[too_long]
        b_len = np.hypot(b[:, 0], b[:, 1])
        b_len[b_len < 1e-9] = 1.0
        mitre[too_long] = b / b_len[:, None]
    return p + mitre * offset


def offset_polygon(points, d):
    """Closed polygon (n, 2) moved outwards by `d` (inwards when negative),
    mitred corners (see offset_polyline)."""
    p = np.asarray(points, np.float64)
    area = 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])
    # the left of the direction of travel is the inside of a ccw polygon
    return offset_polyline(p, -d if area > 0 else d, closed=True)


def _fillet(a, v, b, step):
    """Points of the circular fillet at corner `v` between the planks from
    `a` and to `b`: tangent to both at half the shorter plank from `v`."""
    u_in, u_out = v - a, b - v
    l_in, l_out = np.hypot(*u_in), np.hypot(*u_out)
    u_in, u_out = u_in / l_in, u_out / l_out
    turn = math.atan2(u_in[0] * u_out[1] - u_in[1] * u_out[0], u_in @ u_out)
    t = min(l_in, l_out) / 2.0
    r = t / math.tan(abs(turn) / 2.0)
    p0 = v - u_in * t
    side = 1.0 if turn > 0 else -1.0  # the centre lies on the turn side
    centre = p0 + np.array([-u_in[1], u_in[0]]) * side * r
    start = math.atan2(*(p0 - centre)[::-1])
    m = max(2, int(math.ceil(r * abs(turn) / step)))
    angles = start + turn * np.arange(m + 1) / m
    return centre + r * np.stack([np.cos(angles), np.sin(angles)], axis=1)


def contour(wood, round_below=ROUND_BELOW_DEG, step=STEP_MM):
    """The projected outer contour of a wood polygon: its corners, small
    turns replaced by fillets (see module doc)."""
    n = len(wood)
    limit = math.radians(round_below)
    out = []
    for k in range(n):
        a, v, b = wood[k - 1], wood[k], wood[(k + 1) % n]
        u_in, u_out = v - a, b - v
        turn = abs(math.atan2(u_in[0] * u_out[1] - u_in[1] * u_out[0], u_in @ u_out))
        if 1e-6 < turn < limit:
            out.extend(_fillet(a, v, b, step))
        else:
            out.append(v)
    out = np.array(out)
    # neighbouring fillets (nearly) share their tangent point: drop the
    # repeats and the slivers between them, which an inward offset would
    # turn inside out (a 2 mm chord on a 1 m radius already bends 0.1 deg)
    keep = np.hypot(*(out - np.roll(out, 1, axis=0)).T) > 0.5
    return out[keep]


def build(frame):
    """Shape dict for a `frame:` definition (see module doc)."""
    inner = np.asarray(frame['inner'], np.float64)
    border = float(frame['border'])
    wood = offset_polygon(inner, border)
    outline = contour(wood)
    path = offset_polygon(outline, -border / 2.0)  # the stroke's centre line
    return {
        'polygons': [{'points': outline, 'color': FILL}],
        'strokes': [{'points': np.vstack([path, path[:1]]), 'width': border, 'color': COLOR}],
        'canvas': [inner],
        'round_canvas': [offset_polygon(outline, -border)],
        'corners': inner,
        'wood': wood,
    }


def cache_key(frame):
    return (float(frame['border']), tuple((float(x), float(y)) for x, y in frame['inner']))


def regular_arc(a, b, pieces, length):
    """Corners of a regular polygonal arc of `pieces` straight pieces of
    `length` from point `a` to point `b`, bulging to the left of a -> b
    (upwards for a left-to-right chord): (pieces + 1, 2) including a and b.
    Pieces too short for the chord give a straight line."""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    chord = float(np.hypot(*(b - a)))
    if chord >= pieces * length - 1e-9:
        return a + (b - a) * np.linspace(0.0, 1.0, pieces + 1)[:, None]
    # turn per piece: sin(n a / 2) / sin(a / 2) = chord / length, a in (0, 2 pi / n)
    lo, hi = 1e-9, 2 * math.pi / pieces - 1e-9
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if math.sin(pieces * mid / 2.0) / math.sin(mid / 2.0) > chord / length:
            lo = mid
        else:
            hi = mid
    alpha = (lo + hi) / 2.0
    r = length / (2.0 * math.sin(alpha / 2.0))
    u = (b - a) / chord
    left = np.array([-u[1], u[0]])
    centre = (a + b) / 2.0 - left * r * math.cos(pieces * alpha / 2.0)
    start = math.atan2(*(a - centre)[::-1])
    angles = start - alpha * np.arange(pieces + 1)  # clockwise from a to b
    return centre + r * np.stack([np.cos(angles), np.sin(angles)], axis=1)


def arch_inner(bottom, left, right, pieces, piece, border=94.0):
    """Inner corners of an arch frame from its measured outer sizes (mm):
    the bottom plank, the left and right side planks and the arch of
    `pieces` pieces of `piece` long; the middle of the bottom edge is
    (0, 0). Bottom left, up the left side, over the arch, down the right."""
    half = bottom / 2.0
    a, b = (-half, left), (half, right)
    arc = regular_arc(a, b, pieces, piece)
    wood = np.vstack([[(-half, 0.0)], arc, [(half, 0.0)]])
    return offset_polygon(wood, -border)
