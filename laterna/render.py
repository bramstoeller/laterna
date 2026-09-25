"""Projection rendering: config loading, transforms, molding shading, and
scene rendering (images mapped onto the objects).

Coordinates: world millimetres with (0,0) = bottom centre of the scene
(x negative to the left, y up). The canvas is the projector image
(`canvas` in config.yaml, the projector's native pixels); world (0,0) maps
to the bottom centre of the image, shifted by
`image_offset`.

An object's geometry is a plank frame (`frame:`, see frame.py): the inner
corners of the wood plus the plank width. From that come the screen
polygon (filled, masks the pictures), the molding stroke (shaded with the
relief profile) and the canvas polygon the molding never covers.
"""

import math
import pathlib

import cv2
import numpy as np
import yaml

from . import frame, schema

# Border profiles: cross-section of the molding as [t, height] points, t=0 at
# the outer edge, t=1 at the inner side; height 0..1 is scaled by border.relief.
PROFILES = {
    'round': [[0, 0], [0.25, 0.7], [0.5, 1.0], [0.75, 0.7], [1, 0]],
    'bevel': [[0, 0], [0.6, 1.0], [1, 1.0]],
    'molding': [[0, 0], [0.12, 0.75], [0.3, 0.55], [0.5, 1.0], [0.7, 0.6], [0.9, 0.75], [1, 0.5]],
    # classical picture-frame cross-section, outer edge -> sight edge:
    # steep outer face, fillet, raised torus, bead, deep scotia (cove),
    # inner bead, dip and a small lip at the sight edge
    'picture-frame': [
        [0, 0],
        [0.06, 0.85],
        [0.12, 0.8],
        [0.2, 1.0],
        [0.3, 0.62],
        [0.36, 0.72],
        [0.42, 0.52],
        [0.58, 0.28],
        [0.72, 0.62],
        [0.8, 0.72],
        [0.88, 0.4],
        [0.94, 0.5],
        [1, 0.32],
    ],
    # gilded frame, bolder for a molding of ~25 px: wide outer torus, a
    # shallower cove, a rounded inner rail and a small step at the sight edge
    'gilded': [
        [0, 0],
        [0.05, 0.75],
        [0.15, 1.0],
        [0.3, 0.9],
        [0.42, 0.6],
        [0.55, 0.5],
        [0.68, 0.6],
        [0.78, 0.85],
        [0.86, 0.8],
        [0.93, 0.55],
        [1, 0.45],
    ],
}

_IMAGE_CACHE = {}


def load_config(path):
    path = pathlib.Path(path)
    with open(path) as f:
        cfg = schema.check_config(yaml.safe_load(f), path.name)
    cfg['_dir'] = path.resolve().parent
    return cfg


def _rotate(points, degrees):
    a = math.radians(degrees)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return points @ R.T


# --- object geometry -------------------------------------------------------

_SHAPE_CACHE = {}


def object_shape(cfg, obj):
    """Local geometry of an object from its `frame:` (see frame.build), in
    mm relative to its origin; cached per distinct definition."""
    if 'frame' not in obj:
        raise ValueError(f'object {obj.get("id")}: needs a `frame:` definition')
    key = frame.cache_key(obj['frame'])
    if key not in _SHAPE_CACHE:
        if len(_SHAPE_CACHE) > 64:
            _SHAPE_CACHE.clear()
        _SHAPE_CACHE[key] = frame.build(obj['frame'])
    return _SHAPE_CACHE[key]


def _local_to_world(points, obj):
    p = np.asarray(points, dtype=np.float64) * float(obj.get('scale', 1.0))
    p = _rotate(p, float(obj.get('rotation', 0.0)))
    return p + np.asarray(obj['origin'], dtype=np.float64)


def object_polygons_world(cfg, obj):
    """Screen polygons of an object in world mm."""
    return [_local_to_world(p['points'], obj) for p in object_shape(cfg, obj)['polygons']]


def object_corners_world(cfg, obj):
    """The inner corners of an object's wood (world mm): the control points
    of the shape calibration."""
    return _local_to_world(object_shape(cfg, obj)['corners'], obj)


def object_wood_world(cfg, obj):
    """The outer polygon of an object's wood (world mm)."""
    return _local_to_world(object_shape(cfg, obj)['wood'], obj)


def object_canvas_world(cfg, obj):
    """Canvas polygons of an object (world mm): the picture area, where the
    molding never comes."""
    return [_local_to_world(p, obj) for p in object_shape(cfg, obj)['canvas']]


def object_round_canvas_world(cfg, obj):
    """Round canvas polygons of an object (world mm): the sight edge of the
    fictional round frame, which the frame's shadow follows."""
    return [_local_to_world(p, obj) for p in object_shape(cfg, obj)['round_canvas']]


def object_fills_world(cfg, obj):
    """Screen polygons with their fill colour: [(points world mm, rgb)]. The
    fill shows wherever a scene maps no image or video onto the object."""
    return [
        (_local_to_world(p['points'], obj), tuple(int(c) for c in p['color']))
        for p in object_shape(cfg, obj)['polygons']
    ]


def object_strokes_world(cfg, obj):
    """Molding strokes of an object: [(points world mm, width mm, rgb)];
    the width scales with the object."""
    scale = float(obj.get('scale', 1.0))
    return [
        (_local_to_world(s['points'], obj), s['width'] * scale, tuple(int(c) for c in s['color']))
        for s in object_shape(cfg, obj)['strokes']
    ]


def world_to_px(points_mm, cfg, ss=1):
    """World mm -> canvas pixels (y down), including the global calibration.

    The global transform is image_offset (position), rotation, and
    scale_mm_per_px (the scale: how many stage mm one pixel covers).
    """
    p = np.asarray(points_mm, dtype=np.float64)
    p = _rotate(p, float(cfg.get('rotation', 0.0)))
    p = p - np.asarray(cfg.get('image_offset', [0.0, 0.0]), dtype=np.float64)
    mmpp = float(cfg['scale_mm_per_px'])
    w, h = cfg['canvas']
    x = w / 2.0 + p[:, 0] / mmpp
    y = h - p[:, 1] / mmpp
    return np.stack([x, y], axis=1) * ss


# --- keystone ----------------------------------------------------------------
#
# Everything is drawn in plane pixels: the canvas as if the projector stood
# square on the frames. `keystone:` in config.yaml (four [dx, dy] offsets in
# projector pixels, for the corners top left, top right, bottom right,
# bottom left) moves the corners of that canvas; the homography through
# them takes each finished layer from plane pixels to projector pixels. It
# is applied where the supersampled layers are averaged down (one
# resampling, as before) and where video frames are fitted, so the molding
# width, the shading and the spots stay right on the frames. Without
# keystone (the usual case) nothing is warped at all.

KEYSTONE_CORNERS = ('top left', 'top right', 'bottom right', 'bottom left')


def keystone(cfg, ss=1):
    """3x3 homography from plane to projector pixels at supersampling ss,
    or None when config.yaml has no keystone (or all offsets are 0)."""
    offsets = cfg.get('keystone')
    if not offsets or not np.any(np.asarray(offsets, np.float64)):
        return None
    w, h = cfg['canvas']
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    matrix = cv2.getPerspectiveTransform(src, src + np.float32(offsets)).astype(np.float64)
    if ss != 1:
        scale = np.diag([float(ss), float(ss), 1.0])
        matrix = scale @ matrix @ np.linalg.inv(scale)
    return matrix


def _apply_homography(matrix, xs, ys):
    xs, ys = np.asarray(xs, np.float64), np.asarray(ys, np.float64)
    d = matrix[2, 0] * xs + matrix[2, 1] * ys + matrix[2, 2]
    return (
        (matrix[0, 0] * xs + matrix[0, 1] * ys + matrix[0, 2]) / d,
        (matrix[1, 0] * xs + matrix[1, 1] * ys + matrix[1, 2]) / d,
    )


def to_projector(points_px, cfg, ss=1):
    """Plane pixels (n, 2) -> projector pixels (unchanged without keystone)."""
    matrix = keystone(cfg, ss)
    if matrix is None:
        return points_px
    p = np.asarray(points_px, np.float64)
    return np.stack(_apply_homography(matrix, p[:, 0], p[:, 1]), axis=1)


def to_plane(xs, ys, cfg, ss=1):
    """Projector pixel coordinates -> plane pixel coordinates (floats; the
    input itself without keystone)."""
    matrix = keystone(cfg, ss)
    if matrix is None:
        return xs, ys
    return _apply_homography(np.linalg.inv(matrix), xs, ys)


def keystone_image(image, cfg, ss=1, border=0.0):
    """A plane-pixel image (at supersampling ss) warped to projector pixels,
    `border` where no plane pixel lands; the image itself without keystone."""
    matrix = keystone(cfg, ss)
    if matrix is None:
        return image
    h, w = image.shape[:2]
    return cv2.warpPerspective(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(border,) * 4,
    )


def projector_mask(cfg, oid):
    """Like object_mask (ss 1), in projector pixels: the polygons moved
    through the keystone (a homography keeps them polygons)."""
    w, h = cfg['canvas']
    return _fill_mask((h, w), [to_projector(p, cfg) for p in polys_px(cfg, [oid])])


def _objects(cfg, ids=None):
    if ids is None:
        return list(cfg['objects'])
    by_id = {o['id']: o for o in cfg['objects']}
    return [by_id[i] for i in ids]


def polys_px(cfg, ids=None, ss=1):
    """Screen polygons of the given objects (all when None), in canvas px."""
    return [
        world_to_px(p, cfg, ss) for o in _objects(cfg, ids) for p in object_polygons_world(cfg, o)
    ]


def canvas_px(cfg, ids=None, ss=1):
    """Canvas polygons of the given objects (all when None), in canvas px."""
    return [
        world_to_px(p, cfg, ss) for o in _objects(cfg, ids) for p in object_canvas_world(cfg, o)
    ]


def round_canvas_px(cfg, ids=None, ss=1):
    """Round canvas polygons of the given objects (all when None), in canvas px."""
    return [
        world_to_px(p, cfg, ss)
        for o in _objects(cfg, ids)
        for p in object_round_canvas_world(cfg, o)
    ]


def object_mask(cfg, oid, ss=1):
    """Boolean mask (h, w) of one object: its screen polygons (molding
    included), at the canvas resolution times `ss`."""
    w, h = cfg['canvas']
    return _fill_mask((h * ss, w * ss), polys_px(cfg, [oid], ss))


def strokes_px(cfg, ids=None, ss=1):
    """Molding strokes of the given objects: [(points px, width mm, rgb)]."""
    return [
        (world_to_px(pts, cfg, ss), width, color)
        for o in _objects(cfg, ids)
        for pts, width, color in object_strokes_world(cfg, o)
    ]


# --- molding ---------------------------------------------------------------


def profile_evaluator(spec):
    if isinstance(spec, str):
        spec = PROFILES[spec]
    pts = np.asarray(spec, dtype=np.float64)
    return lambda t: np.interp(t, pts[:, 0], pts[:, 1])


def _fill_mask(shape, polys_px):
    mask = np.zeros(shape, np.uint8)
    for poly in polys_px:
        pts = np.round(np.asarray(poly) * 16).astype(np.int32)
        cv2.fillPoly(mask, [pts], 255, lineType=cv2.LINE_AA, shift=4)
    return mask > 127


def _stroke_geometry(points, width_px, d_out, closed):
    """The band an SVG stroke covers: (fill polygon, hole polygon or None,
    outer edge polyline). A closed path gives a ring: fill minus hole.

    `d_out` is the distance field to the outside of the screen; the side of
    the path that lies closer to the screen edge is the outer side, where
    the relief profile starts. Closed paths use their orientation instead."""
    p = np.asarray(points, np.float64)
    if closed and len(p) > 1 and np.allclose(p[0], p[-1]):
        p = p[:-1]
    half = width_px / 2.0
    if closed:
        area = 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])
        # the left side is the interior for a positive (ccw in these
        # coordinates) area, so outer = right = negative offset
        outer_sign = -1.0 if area > 0 else 1.0
    else:
        h, w = d_out.shape
        probe = max(half * 0.5, 1.0)
        left = frame.offset_polyline(p, probe, False)
        right = frame.offset_polyline(p, -probe, False)

        def sample(q):
            xi = np.clip(np.round(q[:, 0]).astype(int), 0, w - 1)
            yi = np.clip(np.round(q[:, 1]).astype(int), 0, h - 1)
            return d_out[yi, xi].mean()

        outer_sign = 1.0 if sample(left) < sample(right) else -1.0
    outer = frame.offset_polyline(p, outer_sign * half, closed)
    inner = frame.offset_polyline(p, -outer_sign * half, closed)
    if closed:
        return outer, inner, np.vstack([outer, outer[:1]])
    return np.vstack([outer, inner[::-1]]), None, outer


def compute_molding(shape, polys_px, strokes_px, cfg, ss, canvas_px=(), round_canvas_px=None):
    """Molding shading for a set of screen polygons and molding strokes.

    A stroke covers what SVG draws: a band `width` wide centred on its path
    with mitred corners, clipped to the screen polygons. Across the band, t
    runs 0 -> 1 from the outer edge (the side facing the screen edge) to
    the inner edge and feeds the relief profile; distances are measured
    from the outer edge line, so the profile turns the corners along the
    mitre like a real frame. Where bands of different strokes overlap the
    one with the smaller t wins. One distance transform per distinct
    (width, colour) class. A screen edge without a stroke gets no molding.
    `canvas_px` polygons (the frames' picture areas) are cut out of the
    molding: the picture shows there. The shadow is thrown by the fictional
    round frame: the screen minus `round_canvas_px` (the molding when None).

    Returns (inside, molding, image, fade, shading): boolean masks, a
    float32 RGB image with the shaded molding in the band (black elsewhere),
    the per-pixel factor (0..1) the frame's shadow darkens the picture with
    (border.shadow = strength; 0 disables it) and the (shade, highlight)
    terms over the molding pixels (in `molding` order), for recolouring the
    molding with molding_rgb().
    """
    border = cfg['border']
    light = cfg.get('light', {})
    mmpp = float(cfg['scale_mm_per_px']) / ss
    h, w = shape

    inside = _fill_mask(shape, polys_px)

    classes = {}
    for pts, width, color in strokes_px:
        classes.setdefault((round(float(width), 3), tuple(color)), []).append(pts)
    keys = list(classes)
    widths = np.asarray([k[0] for k in keys] or [1.0], np.float32)
    colors = np.asarray([k[1] for k in keys] or [(0, 0, 0)], np.float32)

    # strokes may run off the canvas; pad so distances near the edges are
    # still measured to the real line rather than to a clipped end
    pad = int(math.ceil(widths.max() / mmpp)) + 2

    def distance(zero_mask):
        """Exact distance (mm) to the zero pixels of a padded uint8 image."""
        return (
            cv2.distanceTransform(zero_mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[pad:-pad, pad:-pad]
            * mmpp
        )

    # distance to the outside of the screen: tells which side of a stroke
    # faces the edge
    d_out = distance(np.pad(inside, pad).astype(np.uint8))

    molding = np.zeros(shape, bool)
    best_t = np.full(shape, np.inf, np.float32)  # distance from the outer edge / width
    which = np.zeros(shape, np.uint8)  # class index per pixel
    for k, key in enumerate(keys):
        width_px = widths[k] / mmpp
        fills, holes = [], []
        edges = np.ones((h + 2 * pad, w + 2 * pad), np.uint8)
        for pts in classes[key]:
            closed = len(pts) > 2 and np.allclose(pts[0], pts[-1])
            fill, hole, outer = _stroke_geometry(pts, width_px, d_out, closed)
            fills.append(fill)
            if hole is not None:
                holes.append(hole)
            q = np.round((outer + pad) * 16).astype(np.int32)
            cv2.polylines(edges, [q], False, 0, 1, cv2.LINE_8, shift=4)
        band = _fill_mask(shape, fills)
        if holes:
            band &= ~_fill_mask(shape, holes)
        t = distance(edges) / widths[k]
        closer = t < best_t
        best_t[closer] = t[closer]
        which[closer] = k
        molding |= band

    molding &= inside
    if len(canvas_px):
        molding &= ~_fill_mask(shape, canvas_px)
    t = np.clip(best_t, 0.0, 1.0)
    relief = float(border.get('relief', 40.0))
    profile = profile_evaluator(border.get('profile', 'molding'))
    height = (profile(t) * relief).astype(np.float32)
    # soften the piecewise-linear profile kinks (~1.5 output px) so the
    # molding shades smoothly instead of in facets
    height = cv2.GaussianBlur(height, (0, 0), 1.5 * ss)

    shade, highlight = _shade_relief(height, mmpp, relief, light, ss)

    image = np.zeros(shape + (3,), np.float32)
    shading = (shade[molding], highlight[molding])
    image[molding] = molding_rgb(colors[which[molding]], *shading)

    # the frame stands proud of the picture and casts a soft shadow onto it,
    # away from the light: the silhouette of the round frame (the molding
    # with round sight edges, as it pretends to be) shifted and blurred
    fade = np.ones(shape, np.float32)
    strength = float(border.get('shadow', 0.0))
    if strength > 0:
        lx, ly, lz = _global_light_dir(light)
        horizontal = max(math.hypot(lx, ly), 1e-6)
        length = relief / mmpp / max(lz / horizontal, 0.2)  # px, at the relief height
        shift = np.float32(
            [[1, 0, -lx / horizontal * length], [0, 1, -ly / horizontal * length]]
        )  # away from the light
        frame_mask = molding
        if round_canvas_px is not None:
            frame_mask = inside & ~_fill_mask(shape, round_canvas_px)
        silhouette = cv2.warpAffine(
            frame_mask.astype(np.float32),
            shift,
            (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
        silhouette = cv2.GaussianBlur(silhouette, (0, 0), max(length / 2.5, 0.5))
        fade -= strength * np.clip(silhouette, 0.0, 1.0)
    return inside, molding, image, fade, shading


def molding_rgb(base, shade, highlight):
    """Lit molding colours (n, 3) for a base colour ((3,) or (n, 3)) and the
    (shade, highlight) terms (n,) from compute_molding. Gold: shadows go
    warm brown, highlights towards a warm white. A black base still gets
    the specular glints (black lacquer), so a real blackout skips the
    molding instead of painting it black (see SceneRenderer)."""
    base = np.asarray(base, np.float32)
    return base * shade[:, None] + (0.55 * base + 0.45 * 255.0) * highlight[:, None]


def _global_light_dir(light):
    """Unit vector towards the light, image coordinates (x right, y down,
    z towards the viewer). Azimuth: direction the light comes from; 0 =
    right, 90 = top, 180 = left. Elevation: angle above the plane."""
    az = math.radians(float(light.get('azimuth', 135.0)))
    el = math.radians(float(light.get('elevation', 40.0)))
    return (math.cos(az) * math.cos(el), -math.sin(az) * math.cos(el), math.sin(el))


def _shade_relief(height, mmpp, relief, light, ss):
    """Lighting of a height map (mm) as (shade, highlight): diffuse + ambient
    factor for the base colour, and a Blinn-Phong specular term (0..1) for
    the highlight colour, lit from `light` (azimuth/elevation). Cavities of
    the profile get less ambient light, and a faint patina (low-frequency
    mottling) breaks the uniformity."""
    # Slope (mm per mm) -> normal (-gx, -gy, 1); y in image coordinates (down).
    gx = cv2.Sobel(height, cv2.CV_32F, 1, 0, ksize=3) / (8.0 * mmpp)
    gy = cv2.Sobel(height, cv2.CV_32F, 0, 1, ksize=3) / (8.0 * mmpp)
    norm = np.sqrt(gx * gx + gy * gy + 1.0)

    lx, ly, lz = _global_light_dir(light)
    diffuse = np.clip((-gx * lx - gy * ly + lz) / norm, 0.0, 1.0)
    # half vector between the light and the viewer (straight on)
    hx, hy, hz = lx, ly, lz + 1.0
    hn = math.sqrt(hx * hx + hy * hy + hz * hz)
    n_dot_h = np.clip((-gx * hx - gy * hy + hz) / (norm * hn), 0.0, 1.0)
    shininess = float(light.get('shininess', 24.0))
    highlight = float(light.get('specular', 0.5)) * n_dot_h**shininess

    ambient = float(light.get('ambient', 0.25))
    cavity = 0.5 + 0.5 * np.clip(height / max(relief, 1e-6), 0.0, 1.0)
    shade = ambient * cavity + (1.0 - ambient) * diffuse

    patina = float(light.get('patina', 0.0))
    if patina > 0:
        h, w = height.shape
        rng = np.random.default_rng(7)
        mottle = np.zeros((h, w), np.float32)
        for cell_mm, amp in ((300.0, 1.0), (120.0, 0.5), (40.0, 0.25)):
            cell = max(2, int(cell_mm / mmpp))
            layer = rng.random((h // cell + 2, w // cell + 2)).astype(np.float32)
            mottle += amp * cv2.resize(layer, (w, h), interpolation=cv2.INTER_CUBIC)
        mottle = (mottle / 1.75 - 0.5) * 2.0  # about -1..1
        shade *= 1.0 + patina * mottle
        highlight *= np.clip(1.0 + 2.0 * patina * mottle, 0.0, None)
    return shade, highlight


# --- look: brightness and spotlight -----------------------------------------

LOOK_DEFAULTS = {
    'molding': 1.0,  # brightness of the molding
    'fill': 1.0,  # brightness of the fill colours
    'images': 1.0,  # brightness of mapped images and videos
    'temperature': 6500.0,  # colour temperature of the light (K). Not baked into the
    # render: the lamps apply it at play time (laterna/lamps.py),
    # and the desk's cct channel overrides it
    'white': 6500.0,  # colour temperature of the projector's own white (RGB 255,
    # 255, 255): what its COLOR TEMPERATURE setting gives; the
    # light's colour is made relative to this
    'spot_collapse': 1.0,  # how far the light pool flattens as a lamp dims: 0 = the beam
    # keeps its shape, 1 = at the bottom of the fader the picture is
    # lit evenly, as if the spots died before the rest of the light
    'frame_depth': 0.0,  # mm the cloth sits behind the wood's front: pictures keep
    # off the planks' side faces (needs `projector:`); 0 = off
    # the spot on the pictures and the fill colour (per object, placed
    # relative to its bounding box; fractions: x 0..1 left to right, y 0
    # bottom to 1 top, values outside the box allowed)
    'spot': {
        'type': 'cone',  # 'cone': a lamp close to the frame, fanning out;
        # 'gaussian': a soft oval pool, a spot from afar
        'strength': 0.0,  # 0 = even lighting, 1 = black far from the spot
        'position': [0.5, 1.15],  # cone: the lamp, projected on the surface;
        # gaussian: the centre of the pool
        'aim': [0.5, 0.65],  # cone: where the beam axis hits the surface
        'distance': 0.5,  # cone: lamp distance in front of the surface, as a
        # fraction of the object's width (same fan on a
        # narrow and a wide frame)
        'angle': 28.0,  # cone: beam half-angle (degrees) at which light is gone
        'softness': 0.5,  # cone: penumbra as a fraction of the angle (0 = hard edge)
        'falloff': 1.0,  # cone: distance exponent (2 = physical, lower = gentler)
        'size': [0.3, 0.32],  # gaussian: spread (sigma) as fractions of width/height
        'color': [255, 255, 255],  # tint of the lit part (the colour temperature is separate)
        'images': 1.0,  # how much of the spot falls on images/videos
        'fill': 1.0,  # how much of the spot falls on the fill colour
    },
    # the molding's own spot (same keys); None = the picture spot
    'molding_spot': None,
}


def look_settings(cfg):
    """cfg['look'] completed with LOOK_DEFAULTS."""
    look = {**LOOK_DEFAULTS, **(cfg.get('look') or {})}
    look['spot'] = {**LOOK_DEFAULTS['spot'], **(look.get('spot') or {})}
    if look.get('molding_spot'):
        look['molding_spot'] = {**LOOK_DEFAULTS['spot'], **look['molding_spot']}
    else:
        look['molding_spot'] = None
    return look


def molding_spot(cfg):
    """The spot lighting the molding: look.molding_spot, or look.spot."""
    look = look_settings(cfg)
    return look['molding_spot'] or look['spot']


def _kelvin_rgb(kelvin):
    """Approximate sRGB (0..255) of a black body at `kelvin` (Tanner
    Helland's fit, valid 1000..40000 K)."""
    t = min(max(float(kelvin), 1000.0), 40000.0) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * np.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * np.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255.0
    return np.clip([r, g, b], 0, 255)


def white_gain(kelvin, white=6500.0):
    """RGB gain (3,) that makes white light of `kelvin` K on a projector
    whose own white is `white` K: the brightest channel stays at 1
    (nothing clips; warmer than the projector's white dims blue and
    green, cooler dims red), so kelvin == white gives (1, 1, 1)."""
    gain = _kelvin_rgb(kelvin) / _kelvin_rgb(white)
    return (gain / gain.max()).astype(np.float32)


def spot_ratio(cfg, obj, xs, ys, ss=1, on_molding=False):
    """The even share of an object's light at pixels (xs, ys): (n, 3) of
    (1 - strength) / gain, which is what a lit render must be multiplied
    by to take its spot out and leave flat light. All ones when the object
    has no spot. Used to collapse the pool as a lamp dims (laterna/lamps.py):
    a real spot keeps its beam, but when the spots die before the rest of
    the light the pool flattens first."""
    gain = _spot_gain(cfg, obj, xs, ys, ss, on_images=not on_molding, on_molding=on_molding)
    if gain is None:
        return np.ones((len(xs), 3), np.float32)
    spot = molding_spot(cfg) if on_molding else look_settings(cfg)['spot']
    strength = float(spot['strength']) * (1.0 if on_molding else float(spot['images']))
    return np.clip((1.0 - strength) / np.maximum(gain, 1e-6), 0.0, 1.0).astype(np.float32)


def headroom(cfg):
    """How much brighter than the projector's white the look's gains can
    make a layer (>= 1).

    look.molding/fill/images are allowed above 1, so a lit layer can ask
    for more light than the projector has: a white canvas under
    look.fill 1.5 wants 382 of 255. Clipping that away in the render
    would flatten the spot's gradient for good, and dimming a flat area
    keeps it flat. So the render is stored this much darker (apply_look
    divides the gains by it, nothing clips) and the lamps multiply it
    back at play time (laterna/lamps.py, headroom_gain), which puts the
    clipping after the dimming: at full light the picture is what it
    always was, and as it dims the gradient comes back.
    """
    look = look_settings(cfg)
    return max(1.0, float(look['molding']), float(look['fill']), float(look['images']))


def headroom_gain(cfg):
    """The headroom as a display-space gain (3,): what the lamps multiply
    the render by at full light. It equals the headroom itself, except
    when gamma_lut() corrects a display whose gamma is not GAMMA_TARGET,
    because that LUT is applied after the division."""
    h = headroom(cfg)
    if gamma_lut(cfg) is None:
        return np.full(3, h, np.float32)
    values = np.ravel(np.asarray(cfg['gamma'], np.float64))
    if len(values) == 1:
        values = np.repeat(values, 3)
    return (h ** (GAMMA_TARGET / values)).astype(np.float32)


def full_power(image, cfg):
    """A render as the projector shows it at full light: the headroom the
    lamps give back, clipped like the display clips. For stills that do
    not go through the lamps (tools/make_icon.py)."""
    return np.clip(image.astype(np.float32) * headroom_gain(cfg), 0, 255).astype(np.uint8)


def _spot_gain(cfg, obj, xs, ys, ss, on_images=False, on_fill=False, on_molding=False):
    """RGB gain (n, 3) of the object's pretend spotlight at canvas pixels
    (xs, ys) at supersampling ss, or None when there is no spot.

    Every object gets its own spot, placed relative to its bounding box, that
    leaves its brightest point at full brightness and darkens towards
    (1 - strength) far away, so the frame reads as lit like the rest of a
    theatre set (raise the layer brightness for more punch). Two models:

    - gaussian: a soft elliptical pool, like a spot far away in the rig;
    - cone: a lamp `distance` x object width in front of the surface at `position`,
      aimed at `aim`, with a beam of half-angle `angle` and a penumbra of
      `softness` x angle: light fans out from the lamp, normalised to 1 at
      its brightest point on the object (nearest the lamp, not where the
      axis hits, when the lamp is close), dims with the incidence angle
      and with distance^falloff, and is dark behind the lamp. Closed form
      per pixel, no ray casting.

    On images the strength is scaled by spot.images, on the fill colour by
    spot.fill; the molding uses look.molding_spot when set."""
    spot = molding_spot(cfg) if on_molding else look_settings(cfg)['spot']
    strength = float(spot['strength'])
    if on_images:
        strength *= float(spot['images'])
    if on_fill:
        strength *= float(spot['fill'])
    if strength <= 0:
        return None
    polys = polys_px(cfg, [obj['id']], ss)
    pts = np.vstack(polys)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    px, py = (float(v) for v in spot['position'])
    cx, cy = x0 + px * w, y1 - py * h  # y down
    if spot.get('type', 'gaussian') == 'cone':
        lamp = (
            spot,
            cx,
            cy,
            x0 + float(spot['aim'][0]) * w,
            y1 - float(spot['aim'][1]) * h,
            float(spot['distance']) * w,
        )
        g = _cone_light(xs, ys, *lamp) / _cone_peak(polys, *lamp)
    else:
        sx, sy = (max(float(v), 1e-3) for v in spot['size'])
        g = np.exp(-0.5 * (((xs - cx) / (sx * w)) ** 2 + ((ys - cy) / (sy * h)) ** 2))
    color = np.asarray(spot['color'], np.float32)
    tint = color / max(color.max(), 1.0)
    return ((1.0 - strength) + strength * g.astype(np.float32)[:, None] * tint[None, :]).astype(
        np.float32
    )


def _cone_peak(polys, spot, lx, ly, ax, ay, d):
    """The brightest _cone_light on the object (its screen polygons):
    the maximum over a fixed grid inside them, so every
    caller (fill, images, video, the lamps' spot_ratio) divides by the
    same number whatever pixels it asks for."""
    pts = np.vstack(polys)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    n = 256
    scale = (n - 1) / max(x1 - x0, y1 - y0, 1.0)
    mask = np.zeros((n, n), np.uint8)
    cv2.fillPoly(mask, [np.round((p - (x0, y0)) * scale).astype(np.int32) for p in polys], 1)
    gy, gx = np.nonzero(mask)
    e = _cone_light(gx / scale + x0, gy / scale + y0, spot, lx, ly, ax, ay, d)
    return max(float(e.max()), 1e-6) if e.size else 1.0


def _cone_light(xs, ys, spot, lx, ly, ax, ay, d):
    """Relative illuminance on the surface plane from a lamp at (lx, ly)
    hanging `d` px in front of it, aimed at (ax, ay) — see _spot_gain.
    1 where the axis hits the surface; more closer to the lamp."""
    vx, vy = xs - lx, ys - ly
    r = np.sqrt(vx * vx + vy * vy + d * d)
    bx, by = ax - lx, ay - ly
    r0 = math.sqrt(bx * bx + by * by + d * d)  # lamp -> aim point
    cos_theta = np.clip((vx * bx + vy * by + d * d) / (r * r0), -1.0, 1.0)
    theta = np.arccos(cos_theta)
    angle = math.radians(max(float(spot['angle']), 0.1))
    inner = angle * (1.0 - min(max(float(spot['softness']), 0.0), 1.0))
    t = np.clip((angle - theta) / max(angle - inner, 1e-6), 0.0, 1.0)
    beam = t * t * (3.0 - 2.0 * t)  # smoothstep penumbra
    cos_inc = d / r  # incidence on the plane
    falloff = float(spot['falloff'])
    e = beam * cos_inc * (r0 / r) ** falloff
    return e / (d / r0)  # 1 at the aim point


def image_gain_at(cfg, ids, xs, ys, ss=1):
    """Brightness gain (n, 3) for image/video pixels (xs, ys) inside the
    given objects (look.images / headroom x each object's spot), or None
    when it is 1 everywhere. The light's colour is not in here: the
    render is neutral, the lamps colour it (laterna/lamps.py)."""
    look = look_settings(cfg)
    base = float(look['images']) / headroom(cfg) * np.ones(3, np.float32)  # (3,)
    gain = None
    h, w = (int(v * ss) for v in reversed(cfg['canvas']))
    for obj in _objects(cfg, ids):
        member = _fill_mask((h, w), polys_px(cfg, [obj['id']], ss))[ys, xs]
        spot = _spot_gain(cfg, obj, xs[member], ys[member], ss, on_images=True)
        if spot is None and np.allclose(base, 1.0):
            continue
        if gain is None:
            gain = np.ones((len(xs), 3), np.float32)
        gain[member] = base * (1.0 if spot is None else spot)
    return gain


# --- media mapping ---------------------------------------------------------


def file_key(path):
    """Cache key that changes when the file is rewritten (path, mtime, size),
    so a process that lives long (the menu) picks up regenerated files."""
    st = pathlib.Path(path).stat()
    return (str(path), st.st_mtime_ns, st.st_size)


def _load_image(path):
    path = str(path)
    try:
        key = file_key(path)
    except OSError:
        raise FileNotFoundError(f'image missing or unreadable: {path}')
    if key not in _IMAGE_CACHE:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f'image missing or unreadable: {path}')
        _IMAGE_CACHE.clear()  # older versions of any file are no longer wanted
        _IMAGE_CACHE[key] = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
    return _IMAGE_CACHE[key]


def _resize(img, bw, bh):
    interp = cv2.INTER_AREA if bw < img.shape[1] else cv2.INTER_CUBIC
    return cv2.resize(img, (bw, bh), interpolation=interp)


def _cover(img, bw, bh):
    """Scale preserving aspect ratio until the target is filled, centre-crop."""
    ih, iw = img.shape[:2]
    s = max(bw / iw, bh / ih)
    nw, nh = max(bw, round(iw * s)), max(bh, round(ih * s))
    r = _resize(img, nw, nh)
    x0, y0 = (nw - bw) // 2, (nh - bh) // 2
    return r[y0 : y0 + bh, x0 : x0 + bw]


def sized_explicitly(mapping):
    return 'width' in mapping or 'height' in mapping


MEDIA_KEYS = ('image', 'video', 'slideshow')


def media_paths(mapping):
    """The files a mapping shows: [image], [video] or the slideshow's list."""
    if 'slideshow' in mapping:
        return list(mapping['slideshow'])
    return [mapping['image'] if 'image' in mapping else mapping['video']]


def is_animated(mapping):
    """True for mappings drawn per tick during playback (video, slideshow):
    the static render leaves their polygons black and adds an alpha layer."""
    return 'video' in mapping or 'slideshow' in mapping


VIDEO_EXTENSIONS = ('.mp4', '.m4v', '.mov', '.mkv', '.webm', '.avi', '.mpg', '.mpeg')


def is_video_path(name):
    """A slideshow item is a video when its extension says so; anything
    else is read as an image."""
    return str(name).lower().endswith(VIDEO_EXTENSIONS)


def _clip_convex(subject, clip):
    """Sutherland-Hodgman: the part of polygon `subject` inside the convex
    polygon `clip` (both (n, 2), any orientation)."""
    clip = np.asarray(clip, np.float64)
    area = 0.5 * np.sum(clip[:, 0] * np.roll(clip[:, 1], -1) - np.roll(clip[:, 0], -1) * clip[:, 1])
    sign = 1.0 if area > 0 else -1.0
    out = [tuple(q) for q in np.asarray(subject, np.float64)]
    for a, b in zip(clip, np.roll(clip, -1, axis=0)):

        def inside(q):
            return sign * ((b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])) >= 0

        pts, out = out, []
        for k, q in enumerate(pts):
            prev = pts[k - 1]
            if inside(q):
                if not inside(prev):
                    out.append(_intersect(prev, q, a, b))
                out.append(q)
            elif inside(prev):
                out.append(_intersect(prev, q, a, b))
    return np.array(out) if out else np.zeros((0, 2))


def _intersect(p, q, a, b):
    """Intersection of the line p-q with the line a-b."""
    d1, d2 = (q[0] - p[0], q[1] - p[1]), (b[0] - a[0], b[1] - a[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    t = ((a[0] - p[0]) * d2[1] - (a[1] - p[1]) * d2[0]) / den
    return (p[0] + t * d1[0], p[1] + t * d1[1])


def image_polys_px(cfg, ids=None, ss=1):
    """The projection mask for pictures on the given objects (all when
    None), in canvas px: their canvas polygons (the picture areas).

    With look.frame_depth > 0 and a `projector:` block the strip along the
    wood whose light would miss the cloth (mounted frame_depth behind the
    wood's front) and light the planks' side faces is cut off. A ray
    through a point p of the front plane reaches the cloth at p moved away
    from the projector by depth / distance, so the lit cloth is the canvas
    scaled about the projector's foot point by distance / (distance +
    depth); the mask is the canvas clipped to that. The cloth itself loses
    nothing: it is reached by rays from a little further in."""
    look = look_settings(cfg)
    depth, projector = float(look['frame_depth']), cfg.get('projector')
    out = []
    for o in _objects(cfg, ids):
        for p in object_canvas_world(cfg, o):
            if depth > 0 and projector:
                foot = np.asarray(projector['position'], np.float64)
                distance = float(projector['distance'])
                p = _clip_convex(p, foot + (p - foot) * (distance / (distance + depth)))
            out.append(world_to_px(p, cfg, ss))
    return out


def canvas_rect_px(cfg, ids=None, ss=1):
    """Bounding box (x0, y0, x1, y1, float px) of the canvases of the given
    objects (all when None): the box pictures are fitted to. For several
    objects it spans them all, so the media aligns with the outermost
    visible canvas edges."""
    return _bbox(
        [world_to_px(p, cfg, ss) for o in _objects(cfg, ids) for p in object_canvas_world(cfg, o)]
    )


def _bbox(polys):
    points = np.vstack(polys)
    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0)
    return float(x0), float(y0), float(x1), float(y1)


def mapping_rect(mapping, ref_rect, source_size, mmpp):
    """Pixel rect (x0, y0, x1, y1) the mapped media occupies.

    The reference `ref_rect` is the canvas box (canvas_rect_px) of the
    mapping's own objects, or of its `fit:` list. Without explicit sizes the
    media is cover-fitted onto that box, so the picture fills the visible
    canvas and only the molding's own corners hide any of it. `width:`/
    `height:` (world mm) give the media exactly that size — whether it
    covers the polygons or not — centred on the reference box; a single
    dimension keeps the source aspect ratio (source_size = (w, h) of the
    medium), both stretch.
    """
    x0, y0 = np.floor(ref_rect[:2]).astype(int)
    x1, y1 = np.ceil(ref_rect[2:]).astype(int)
    if not sized_explicitly(mapping):
        return x0, y0, x1, y1
    iw, ih = source_size
    tw = round(float(mapping['width']) / mmpp) if 'width' in mapping else None
    th = round(float(mapping['height']) / mmpp) if 'height' in mapping else None
    if tw is None:
        tw = round(th * iw / ih)
    if th is None:
        th = round(tw * ih / iw)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    nx0, ny0 = cx - tw // 2, cy - th // 2
    return nx0, ny0, nx0 + tw, ny0 + th


def _draw_mapping(canvas, mapping, polys_px, ref_rect, cfg, ss=1):
    """Draw one image over the mapping's rect (see mapping_rect), masked to
    the polygons themselves. A missing image is skipped with a warning so
    the show still runs; those polygons stay black."""
    try:
        source = _load_image(cfg['_dir'] / mapping['image'])
    except FileNotFoundError:
        print(f'warning: {mapping["image"]} missing, objects {mapping["objects"]} stay black')
        return
    h, w = canvas.shape[:2]
    mask = _fill_mask((h, w), polys_px)
    mmpp = float(cfg['scale_mm_per_px']) / ss
    x0, y0, x1, y1 = mapping_rect(mapping, ref_rect, (source.shape[1], source.shape[0]), mmpp)
    fit = _resize if sized_explicitly(mapping) else _cover
    image = fit(source, x1 - x0, y1 - y0)
    cx0, cy0 = max(x0, 0), max(y0, 0)
    cx1, cy1 = min(x1, w), min(y1, h)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    sub = mask[cy0:cy1, cx0:cx1]
    canvas[cy0:cy1, cx0:cx1][sub] = image[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0][sub]


def load_scenes(path):
    """The slideshow (scenes.yaml): hand-written, separate from the object config."""
    with open(path) as f:
        return yaml.safe_load(f)


def parse_scenes(data, cfg):
    """Scenes and fades from the scenes data (from load_scenes), checked
    against schema.Scenes: unknown keys, wrong values and object ids that
    config.yaml does not have are errors naming the scene.

    Timing, in the words of a slideshow mapping: the top of scenes.yaml
    gives the defaults `hold` (seconds a scene stays before the show moves
    on by itself; none = it waits for a key), `transition` (only `fade`)
    and `transition_time` (seconds the fade to the next scene takes; the
    older `fade:` means the same). A scene overrides them with the same
    keys, and a `- fade: <seconds>` entry between two scenes still sets
    that one transition (not together with the previous scene's
    transition_time). Every scene comes out with `hold` (float or None)
    and `transition_time` filled in.

    A blackout scene (`blackout: true`) is all black; its fade dims
    everything together, like a master fader (play.py).

    Returns (scenes, fades) with fades[i] = duration between scene i and i+1.
    """
    data = schema.check_scenes(data, [o['id'] for o in cfg['objects']])
    top = dict(data)
    if 'fade' in top:
        top['transition_time'] = top.pop('fade')
    timing = parse_timing(top, {'hold': None, 'transition': 'fade', 'transition_time': 1.0})
    scenes, fades, pending = [], [], None
    for entry in data['scenes']:
        if 'fade' in entry:  # a `- fade:` entry between two scenes
            pending = entry['fade']
            continue
        entry = {**entry, **parse_timing(entry, timing)}
        if entry.get('blackout'):
            entry['mappings'] = []
        for key in SCENE_COLORS:
            if key in entry:
                entry[key] = tuple(entry[key])
        if scenes:
            previous = scenes[-1]
            if pending is not None and 'transition_time' in previous.get('_own', ()):
                raise ValueError(
                    f'scene {previous.get("name")} sets transition_time '
                    f'and is followed by a `- fade:` entry: pick one'
                )
            fades.append(previous['transition_time'] if pending is None else pending)
        pending = None
        scenes.append(entry)
    return scenes, fades


# scene-level colours: the molding's base colour and the fill of the
# unmapped objects, [r, g, b] 0..255 (defaults: frame.COLOR, frame.FILL)
SCENE_COLORS = ('molding_color', 'fill_color')

TIMING_KEYS = ('hold', 'transition', 'transition_time')


def parse_timing(node, defaults):
    """The scene timing keys of a scenes node (the file's top or a scene),
    completed from `defaults`: {'hold': seconds or None, 'transition':
    'fade', 'transition_time': seconds, '_own': the keys the node set
    itself}."""
    out = dict(defaults)
    own = set()
    for key in TIMING_KEYS:
        if key in node:
            out[key] = node[key]
            own.add(key)
    out['_own'] = own
    return out


# slideshow mapping: `hold` seconds per image, then a `transition` of
# `transition_time` seconds to the next; the same for every image of the show
SLIDESHOW_DEFAULTS = {'hold': 5.0, 'transition': 'fade', 'transition_time': 1.0}
SLIDESHOW_TRANSITIONS = ('fade',)


class SceneRenderer:
    """Renders scenes efficiently for playback.

    The molding is identical for every scene, so its shading (the expensive
    part: distance transforms and lighting) is computed once in the
    constructor and reused for each scene.

    Layering per scene: the objects' fill colours where the scene maps
    nothing, the scene's images fitted to the canvas inside the molding
    (canvas_rect_px) and masked to the polygons, the frame's shadow
    on them, then the molding on top. Without a scene (preview) every
    object shows its fill. Brightness and the pretend spotlights
    (cfg['look']) are applied per layer in apply_look(); they are gains,
    kept apart from the colours, so a scene can recolour the molding
    (`molding_color`) or the fill (`fill_color`) cheaply. A scene with
    `blackout: true` renders all black, molding included.
    """

    def __init__(self, cfg, ss=3):
        self.cfg = cfg
        self.ss = ss
        self.out_size = tuple(cfg['canvas'])
        w, h = self.out_size
        self.shape = (h * ss, w * ss)
        self.polys = {o['id']: polys_px(cfg, [o['id']], ss) for o in cfg['objects']}
        self.rects = {}  # tuple of ids -> canvas box inside the molding
        self.lut = gamma_lut(cfg)
        inside, molding, image, fade, shading = compute_molding(
            self.shape,
            polys_px(cfg, None, ss),
            strokes_px(cfg, None, ss),
            cfg,
            ss,
            canvas_px(cfg, None, ss),
            round_canvas_px(cfg, None, ss),
        )
        flat = image.reshape(-1, 3)
        self.molding_idx = np.flatnonzero(molding)  # opaque molding
        self.molding_base = flat[self.molding_idx]  # as shaded, unlit by the look
        self.molding_shading = shading  # (shade, highlight): recolouring
        self.molding_recolored = {}  # rgb -> shaded, unlit colours
        # a fixed dither pattern (one step, the same for every render, so a
        # scene and its molding still cancel exactly in laterna/lamps.py): the
        # rounding to 8 bits then becomes noise instead of contours, which
        # matters because the headroom stores the render that much darker
        self.dither = np.random.default_rng(0).random(
            (self.out_size[1], self.out_size[0], 1), dtype=np.float32
        )
        self.inside_idx = {}  # object id -> flat indices of its screen
        self.fill_base = {}  # object id -> [(flat indices, rgb)] per polygon
        for o in cfg['objects']:
            self.inside_idx[o['id']] = np.flatnonzero(_fill_mask(self.shape, self.polys[o['id']]))
            self.fill_base[o['id']] = [
                (
                    np.flatnonzero(_fill_mask(self.shape, [world_to_px(pts, cfg, ss)])),
                    np.asarray(color, np.float32),
                )
                for pts, color in object_fills_world(cfg, o)
            ]
        self.fade_idx = np.flatnonzero(inside & ~molding & (fade < 1.0))
        self.fade = fade.reshape(-1)[self.fade_idx]  # frame shadow on the image
        self.apply_look()

    def apply_look(self):
        """(Re)compute the lit colours from cfg['look']: brightness per
        layer and each object's spotlight (the light's colour temperature
        is the lamps' business, not the render's), divided by the
        headroom. Cheap compared to the molding shading, so an
        interactive app can call it after every change."""
        cfg, ss, w = self.cfg, self.ss, self.shape[1]
        look = look_settings(cfg)
        # the picture masks depend on look.frame_depth; the strip of canvas
        # they leave out (the parallax strip) stays black, whatever is shown
        self.image_polys = {o['id']: image_polys_px(cfg, [o['id']], ss) for o in cfg['objects']}
        self.strip_idx = np.flatnonzero(
            _fill_mask(self.shape, canvas_px(cfg, None, ss))
            & ~_fill_mask(self.shape, self._image_px([o['id'] for o in cfg['objects']]))
        )
        # the render is neutral (6500 K): the light's colour temperature
        # (look.temperature, the desk's cct) is the lamps' (laterna/lamps.py).
        # Every gain is divided by the headroom, so nothing clips here and
        # the lamps can dim before the clipping (see headroom())
        white = np.ones(3, np.float32) / headroom(cfg)
        # gains (n, 3) over molding_idx and per fill polygon: brightness x
        # spot; the colours come in per scene (_compose_ss)
        self.molding_gain = np.tile(float(look['molding']) * white, (len(self.molding_idx), 1))
        self.fill_gain = {}  # object id -> [(flat indices, gain (n, 3))]
        self.image_gain = {}  # object id -> (n, 3) gain over inside_idx, or None
        for o in cfg['objects']:
            oid = o['id']
            member = np.isin(self.molding_idx, self.inside_idx[oid], assume_unique=True)
            ys, xs = np.divmod(self.molding_idx[member], w)
            spot = _spot_gain(cfg, o, xs, ys, ss, on_molding=True)
            if spot is not None:
                self.molding_gain[member] *= spot
            fills = []
            for idx, _ in self.fill_base[oid]:
                ys, xs = np.divmod(idx, w)
                spot = _spot_gain(cfg, o, xs, ys, ss, on_fill=True)
                gain = float(look['fill']) * white * (1.0 if spot is None else spot)
                fills.append((idx, gain))
            self.fill_gain[oid] = fills
            ys, xs = np.divmod(self.inside_idx[oid], w)
            self.image_gain[oid] = image_gain_at(cfg, [oid], xs, ys, ss)

    def _px(self, ids):
        return [p for i in ids for p in self.polys[i]]

    def _image_px(self, ids):
        return [p for i in ids for p in self.image_polys[i]]

    def _rect(self, ids):
        key = tuple(ids)
        if key not in self.rects:
            self.rects[key] = canvas_rect_px(self.cfg, list(ids), self.ss)
        return self.rects[key]

    def molding_colors(self, color=None):
        """Lit molding colours (n, 3) over molding_idx: the strokes' own
        colours, or `color` (rgb) shaded like them."""
        if color is None:
            base = self.molding_base
        else:
            key = tuple(color)
            if key not in self.molding_recolored:
                self.molding_recolored[key] = molding_rgb(key, *self.molding_shading)
            base = self.molding_recolored[key]
        return base * self.molding_gain

    def _compose_ss(self, scene):
        """Supersampled float32 canvas for a scene. Video mappings stay black
        here: their polygons are filled per frame during playback (laterna/video.py),
        so the static render doubles as the premultiplied colour layer.
        A blackout scene is black throughout."""
        canvas = np.zeros(self.shape + (3,), np.float32)
        if scene is not None and scene.get('blackout'):
            return canvas
        flat = canvas.reshape(-1, 3)
        scene_fill = (scene or {}).get('fill_color')
        mapped = {i for m in (scene or {}).get('mappings', []) for i in m['objects']}
        for oid, fills in self.fill_gain.items():
            if oid not in mapped:
                for (idx, gain), (_, color) in zip(fills, self.fill_base[oid]):
                    base = color if scene_fill is None else np.asarray(scene_fill, np.float32)
                    flat[idx] = base * gain
        flat[self.strip_idx] = 0.0
        if scene is not None:
            for m in scene.get('mappings', []):
                if 'image' not in m:
                    continue
                _draw_mapping(
                    canvas,
                    m,
                    self._image_px(m['objects']),
                    self._rect(m.get('fit', m['objects'])),
                    self.cfg,
                    self.ss,
                )
                for oid in m['objects']:  # brightness + spot on the image
                    if self.image_gain[oid] is not None:
                        flat[self.inside_idx[oid]] *= self.image_gain[oid]
        # the frame's shadow on the picture, then the molding on top
        flat[self.fade_idx] *= self.fade[:, None]
        flat[self.molding_idx] = self.molding_colors((scene or {}).get('molding_color'))
        return canvas

    def _quantise(self, out):
        """Float canvas (0..255) -> uint8, rounded with self.dither."""
        return np.clip(out + self.dither, 0, 255).astype(np.uint8)

    def _down(self, canvas, border=0.0):
        """A supersampled plane layer -> projector pixels at the output
        size: through the keystone (if any), then averaged down."""
        canvas = keystone_image(canvas, self.cfg, self.ss, border)
        return cv2.resize(canvas, self.out_size, interpolation=cv2.INTER_AREA)

    def render(self, scene=None):
        out = self._down(self._compose_ss(scene))
        return apply_gamma(self._quantise(out), self.lut)

    def render_molding(self, scene=None):
        """The scene's molding alone, everything else black (all black for
        a blackout): what splits a render into pictures and molding, so the
        lamps (laterna/lamps.py) can dim them apart."""
        canvas = np.zeros(self.shape + (3,), np.float32)
        if scene is None or not scene.get('blackout'):
            flat = canvas.reshape(-1, 3)
            flat[self.molding_idx] = self.molding_colors((scene or {}).get('molding_color'))
        out = self._down(canvas)
        return apply_gamma(self._quantise(out), self.lut)

    def video_alpha(self, scene):
        """Coverage of the static render over a scene's video and slideshow
        regions, as a uint8 layer (255 = static wins, 0 = video fully
        visible), or None when the scene maps no videos or slideshows.

        Built at the supersampling and averaged down like the colours, so
        the fractional edge values carry the anti-aliasing. Inside a video
        polygon the alpha is 0, the frame's shadow raises it (darkening by
        a factor equals compositing black at alpha 1 - fade) and the molding
        is opaque on top. render() leaves video polygons black,
        which is exactly colour x alpha (premultiplied), so playback is
        one blend: video * (1 - alpha) + render."""
        video_maps = [m for m in scene.get('mappings', []) if is_animated(m)]
        if not video_maps:
            return None
        alpha = np.ones(self.shape, np.float32)
        for m in video_maps:
            alpha[_fill_mask(self.shape, self._image_px(m['objects']))] = 0.0
        flat = alpha.reshape(-1)
        flat[self.fade_idx] = np.maximum(flat[self.fade_idx], 1.0 - self.fade)
        flat[self.molding_idx] = 1.0
        out = self._down(alpha, border=1.0)  # outside the warped canvas: no video
        return (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def render_scene(cfg, scene=None, ss=3):
    """One-off render; for many scenes reuse a SceneRenderer instead."""
    return SceneRenderer(cfg, ss).render(scene)


def render_canvas(cfg, ss=3):
    """Just the frames, without scene images."""
    return render_scene(cfg, None, ss)


def save_png(image_rgb, path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)):
        raise OSError(f'could not write {path}')


GAMMA_TARGET = 2.2


def gamma_lut(cfg):
    """Per-channel uint8 LUT correcting the measured display gamma to
    GAMMA_TARGET, or None when no correction applies.

    cfg['gamma'] holds the display gamma as measured with the chart in
    dynamic_range.py: one number, or [r, g, b] when the channels differ.
    """
    measured = cfg.get('gamma')
    if not measured:
        return None
    values = np.ravel(np.asarray(measured, np.float64))
    if len(values) == 1:
        values = np.repeat(values, 3)
    if np.allclose(values, GAMMA_TARGET, atol=1e-3):
        return None
    ramp = np.arange(256, dtype=np.float64) / 255.0
    lut = np.stack([255.0 * ramp ** (GAMMA_TARGET / g) for g in values], axis=1)
    return np.clip(np.round(lut), 0, 255).astype(np.uint8)


def apply_gamma(image, lut):
    """Apply a gamma_lut() table in place; a None lut is a no-op."""
    if lut is not None:
        for c in range(3):
            image[..., c] = lut[image[..., c], c]
    return image


def global_pixel_affine(cfg):
    """2x3 pixel affine equivalent to the global calibration in world_to_px.

    Maps identity-view pixels (global scale 1, rotation 0, no offset) to
    calibrated pixels, so a screen-space image such as a test pattern can be
    shown with exactly the calibration that the objects get.
    """
    w, h = cfg['canvas']
    mmpp = float(cfg['scale_mm_per_px'])
    src = np.float32([[0, 0], [w, 0], [0, h]])
    world = np.stack([(src[:, 0] - w / 2.0) * mmpp, (h - src[:, 1]) * mmpp], axis=1)
    dst = world_to_px(world, cfg).astype(np.float32)
    return cv2.getAffineTransform(src, dst)
