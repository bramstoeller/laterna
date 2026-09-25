"""The pretend lamps on the set: per object one on its canvas (image,
video or fill colour) and one on its molding, dimmed per tick on the
rendered scene, no re-rendering. The blackout fades (play.dim), the
crossfades between scenes (play.crossfade) and the light desk
(laterna/dmx.py) all come through here.

Compositing: a scene's render is its canvases plus its molding, and the
molding-only render (SceneRenderer.render_molding) splits them: canvas =
render - molding. Per object both parts are cut with its mask (its outer
polygon, dilated 2 px so the anti-aliased rim comes along), multiplied by
level x tint and summed onto black. A crossfade mixes the two scenes (and
their moldings) first, in the same pass. All of it within the objects'
bounding boxes (about 0.7 Mpx of the 2.3 Mpx canvas), in row bands spread
over a few threads (numpy and OpenCV let go of the GIL while they work).

Everything per tick is computed in float and rounded to 8 bits once, at
the end, with a fixed noise of one output step added first (dithering).
The renders are 8-bit but dithered themselves, so on average every pixel
is right; rounding them again after an 8-bit multiply (as pygame blend
blits do) breaks a gradient into contour rings, which grain can only
partly hide. One rounding, dithered by exactly one step, leaves none, and
the grain stays as fine as the render's own. Black stays black: the noise
is below one step and the rounding goes down.

Dimming (lamp_gain): the lamps are tungsten theatre spots. The level
falls linearly in display values (a dimmer's fader) and the colour
follows the filament: its colour temperature goes with V^0.42 while its
light output goes with V^3.4, so CCT ~ L^0.12 in linear light (3200 K is
2650 K at half fader, 1700 K at a tenth, a red glow just above black).
The render is neutral: RGB 255 is the projector's own white, whose
colour temperature is look.white (6500 by default; what the projector's
COLOR TEMPERATURE setting gives). The light's colour is applied here,
relative to it: the desk sets the lamps' colour temperature at full (the
cct channel, default look.temperature) and the dimming reddens from
there. white_gain keeps the brightest channel at 1, so warm light takes
blue and green away, cool light red, 5500 K is as bright as 3200 K,
nothing is corrected twice and nothing clips.

The render is also stored darker by the look's headroom (render.headroom),
which the lamps give back here, so the clipping happens after the
dimming: a spot that flattens into a plateau at full light shows its
gradient again as it dims, instead of staying a flat patch.

As a lamp dims its pool of light flattens (see Lamps._gains and
render.spot_ratio, strength look.spot_collapse): a real spot keeps its
beam while it dims, but in a theatre the spots die before the rest of
the light, so the bright patch goes first and the picture ends up lit
evenly before it goes out. Without it a dimmed spot keeps a flat, over
bright centre, which is what the light desk's master fader shows. A
picture with `spot: false` has no pool to flatten: on its objects the
light is even at every level (Lamps.light's `no_spot`).
"""

import functools
import os
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pygame

from . import render

TUNGSTEN = 0.42 / 3.4  # CCT ~ (linear light) ^ this, for a filament
BAND = 64  # rows per work item: fewer calls, still work for every thread
THREADS = max(1, min(4, os.cpu_count() or 1))  # more only fight over memory

_pool = None


def _executor():
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(THREADS, thread_name_prefix='lamps')
    return _pool


@functools.lru_cache(maxsize=8192)
def lamp_gain(level, kelvin, white, amp):
    """The lamp as a gain per channel (3,): a tungsten lamp of `kelvin` K
    at full, at `level` (1 = full, 0 = out), on a projector whose white is
    `white` K, times the headroom `amp` (a gain per channel). Above 1
    where the headroom gives light back; the clipping comes after."""
    level = min(max(level, 0.0), 1.0)
    linear = level**render.GAMMA_TARGET
    k = kelvin * linear**TUNGSTEN if linear > 0 else 1000.0
    tint = render.white_gain(min(max(k, 1000.0), 40000.0), white)
    return np.float32([level * t * a for t, a in zip(tint, amp)])


def spot_free(scene):
    """The ids of the objects a scene shows a picture on with `spot: false`."""
    return frozenset(
        i for m in scene.get('mappings', []) if m.get('spot') is False for i in m['objects']
    )


def _as_surface(image):
    """A pygame surface on an RGB uint8 image (h, w, 3), no copy."""
    return pygame.image.frombuffer(np.ascontiguousarray(image), image.shape[1::-1], 'RGB')


class Lamps:
    def __init__(self, cfg):
        self.white = float(render.look_settings(cfg)['white'])  # the projector's white
        self.amp = tuple(float(v) for v in render.headroom_gain(cfg))
        self.plain = all(abs(a - 1.0) < 1e-6 for a in self.amp)  # no headroom to give back
        self.collapse = float(render.look_settings(cfg)['spot_collapse'])
        w, h = cfg['canvas']
        # the dither: one output step, fixed (grain that does not crawl) and
        # the same in all three channels (noise in brightness, not in colour)
        self.noise = np.random.default_rng(1).random((h, w, 1), dtype=np.float32)
        self.out = np.zeros((h, w, 3), np.uint8)  # black outside the objects
        self.out_surface = _as_surface(self.out)
        # per object, in its bounding box: the mask (h, w, 1) and the mask
        # times what takes the spot out (render.spot_ratio), on the picture
        # and on the molding (h, w, 3)
        self.objects = {}  # object id -> (rows, cols, mask, picture ratio, molding ratio)
        for o in cfg['objects']:
            # projector pixels, like the renders the lamps get (keystone)
            mask = render.projector_mask(cfg, o['id']).astype(np.uint8)
            mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
            ys, xs = np.nonzero(mask)
            if not len(xs):
                continue  # off the canvas
            rows, cols = slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1)
            m = mask[rows, cols, None].astype(np.float32)
            gy, gx = np.mgrid[rows, cols]
            gx, gy = render.to_plane(gx.ravel(), gy.ravel(), cfg)  # where the spots are
            ratios = [
                render.spot_ratio(cfg, o, gx, gy, on_molding=on_molding)
                .reshape(m.shape[0], m.shape[1], 3)
                .astype(np.float32)
                * m
                for on_molding in (False, True)
            ]
            self.objects[o['id']] = (rows, cols, m, *ratios)
        self.items = self._work_items(h)

    def _work_items(self, h):
        """The work, in bands of BAND rows: per band the stretches of columns
        where objects' boxes overlap (usually one per object), each with
        its objects as (id, slices into the object's arrays, slices into
        the stretch)."""
        items = []
        for y0 in range(0, h, BAND):
            y1 = min(y0 + BAND, h)
            spans = sorted(
                (c.start, c.stop, oid)
                for oid, (r, c, *_) in self.objects.items()
                if r.start < y1 and r.stop > y0
            )
            groups = []  # [x0, x1, [ids]]
            for x0, x1, oid in spans:
                if groups and x0 < groups[-1][1]:
                    groups[-1][1] = max(groups[-1][1], x1)
                    groups[-1][2].append(oid)
                else:
                    groups.append([x0, x1, [oid]])
            for x0, x1, ids in groups:
                parts = []
                for oid in ids:
                    r, c = self.objects[oid][:2]
                    a, b = max(r.start, y0), min(r.stop, y1)
                    local = (
                        slice(a - r.start, b - r.start),
                        slice(c.start - c.start, c.stop - c.start),
                    )
                    inside = (slice(a - y0, b - y0), slice(c.start - x0, c.stop - x0))
                    parts.append((oid, local, inside))
                items.append((slice(y0, y1), slice(x0, x1), parts))
        return items

    def _gains(self, levels):
        """Per object the per-channel factors of its two gains, or None when
        it is out: the picture's gain is ratio x g1 + mask x g2, and the
        molding's the same with its own ratio. As a lamp dims its pool
        flattens: a share of the light keeps its spot, the rest does not
        (ratio = the spot taken out), the share falling with the level."""
        gains = {}
        for oid, (canvas, mold, kelvin) in levels.objects.items():
            if oid not in self.objects or (canvas <= 0 and mold <= 0):
                continue
            pair = []
            for level in (canvas, mold):
                if level <= 0:
                    pair.append(None)
                    continue
                share = 1.0 - self.collapse * (1.0 - min(max(level, 0.0), 1.0))
                g = lamp_gain(level, kelvin, self.white, self.amp)
                pair.append(((1.0 - share) * g, share * g))
            gains[oid] = pair
        return gains

    def _band(self, item, live, molding, fade, gains, no_spot):
        rows, cols, parts = item
        if fade is None:
            pic = live[rows, cols].astype(np.float32)
            mold = molding[rows, cols].astype(np.float32)
        else:
            live_b, molding_b, t = fade
            pic = cv2.addWeighted(
                live[rows, cols], 1.0 - t, live_b[rows, cols], t, 0.0, dtype=cv2.CV_32F
            )
            if molding_b is molding:
                mold = molding[rows, cols].astype(np.float32)
            else:
                mold = cv2.addWeighted(
                    molding[rows, cols], 1.0 - t, molding_b[rows, cols], t, 0.0, dtype=cv2.CV_32F
                )
        pic -= mold  # the pictures alone
        np.maximum(pic, 0.0, out=pic)
        acc = np.empty(pic.shape, np.float32)
        acc[:] = self.noise[rows, cols]
        for oid, local, inside in parts:
            if oid not in gains:
                continue
            _, _, m, ratio_pic, ratio_mold = self.objects[oid]
            if oid in no_spot:
                ratio_pic = m  # an evenly lit picture: nothing to take out
            m = m[local]
            for part, ratio, gain in (
                (pic, ratio_pic, gains[oid][0]),
                (mold, ratio_mold, gains[oid][1]),
            ):
                if gain is not None:
                    g = ratio[local] * gain[0]
                    g += m * gain[1]
                    g *= part[inside]
                    acc[inside] += g
        np.clip(acc, 0.0, 255.0, out=acc)
        self.out[rows, cols] = acc  # truncates: with the noise, a dithered rounding

    def light(self, dst, live, molding, levels, fade=None, no_spot=frozenset()):
        """Draw `live` (the scene as rendered, videos composited: an RGB
        uint8 image (h, w, 3)) onto the surface `dst` under the lamps at
        `levels`; `molding` is the scene's molding-only render. `fade` =
        (live, molding, t) of the scene being faded to, t running 0 -> 1:
        the two are mixed before the light. `no_spot`: the objects whose
        picture has no spot (spot_free), lit evenly as they dim."""
        if fade is None and self.plain and levels.is_full(self.white):
            dst.blit(_as_surface(live), (0, 0))
            return
        gains = self._gains(levels)
        list(
            _executor().map(
                lambda item: self._band(item, live, molding, fade, gains, no_spot), self.items
            )
        )
        dst.blit(self.out_surface, (0, 0))
