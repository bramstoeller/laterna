"""The pretend lamps on the set: per object one on its canvas (image,
video or fill colour) and one on its molding, dimmed per tick with pygame
blend blits on the rendered stage, no re-rendering. The blackout fades
(play.dim) and the light desk (laterna/dmx.py) both come through here.

Compositing: a stage's render is its canvases plus its molding, and the
molding-only render (StageRenderer.render_molding) splits them: canvas =
render - molding. Per object both parts are cut with its mask (its outer
polygon, dilated 2 px so the anti-aliased rim comes along), multiplied by
level x tint and summed onto black. All of it within the objects'
bounding boxes: about 0.7 Mpx of blits per tick on the 2.3 Mpx canvas.

Dimming (dim_fills): the lamps are tungsten theatre spots. The level
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
gradient again as it dims, instead of staying a flat patch. A multiplier
above 1 does not fit in one blit (pygame multiplies by at most 1), so it
is split into a full copy plus a weaker second one that is added on top.

As a lamp dims its pool of light flattens (see _collapse and
render.spot_ratio, strength look.spot_collapse): a real spot keeps its
beam while it dims, but in a theatre the spots die before the rest of
the light, so the bright patch goes first and the picture ends up lit
evenly before it goes out. Without it a dimmed spot keeps a flat, over
bright centre, which is what the light desk's master fader shows.

Deep dims are dithered (see _grain). Multiplying an 8-bit render by a
small number leaves few output values, and without help the gradients
break into contour rings: at a tenth of full light a flat band on a
canvas ran 154 pixels wide. So before the multiply the part gets uniform
noise of one output step, which makes the rounding land on either side
in proportion, and the rings become invisible grain.
"""

import functools
import math

import cv2
import numpy as np
import pygame

from . import render, ui

TUNGSTEN = 0.42 / 3.4  # CCT ~ (linear light) ^ this, for a filament


@functools.lru_cache(maxsize=8192)
def grain_amplitude(fills):
    """How much noise to add before multiplying by `fills`, per channel:
    one output step is 1/m source values for a multiplier m, rounded up to
    a power of two (so a fade reuses a handful of scaled patterns). 0 for a
    channel whose steps are already one value or less: noise there would
    only brighten it."""
    first, second = fills
    total = [(f + s) / 255.0 for f, s in zip(first, second or (0, 0, 0))]
    amps = [min(1 << max(0, math.ceil(math.log2(1.0 / v))), 128) if v > 0 else 128 for v in total]
    return tuple(0 if a < 2 else a for a in amps)


@functools.lru_cache(maxsize=8192)
def dim_fills(level, kelvin, white, amp):
    """The lamp as fill colours to blit with: a tungsten lamp of `kelvin`
    K at full, at `level` (1 = full, 0 = out), on a projector whose white
    is `white` K, times the headroom `amp` (a gain per channel).

    Returns (first, second): `first` multiplies the part, `second` is a
    second copy to add on top for what did not fit in a multiplier of 1,
    or None when one blit is enough.
    """
    level = min(max(level, 0.0), 1.0)
    linear = level**render.GAMMA_TARGET
    k = kelvin * linear**TUNGSTEN if linear > 0 else 1000.0
    tint = render.white_gain(min(max(k, 1000.0), 40000.0), white)
    m = [level * t * a for t, a in zip(tint, amp)]
    first = tuple(int(round(255.0 * min(v, 1.0))) for v in m)
    rest = [min(max(v - 1.0, 0.0), 1.0) for v in m]
    second = tuple(int(round(255.0 * v)) for v in rest) if any(rest) else None
    return first, second


class Lamps:
    def __init__(self, cfg):
        self.white = float(render.look_settings(cfg)['white'])  # the projector's white
        self.amp = tuple(float(v) for v in render.headroom_gain(cfg))
        self.plain = all(abs(a - 1.0) < 1e-6 for a in self.amp)  # no headroom to give back
        w, h = cfg['canvas']
        # the dither pattern, fixed (grain that does not crawl) and the same
        # in all three channels (noise in brightness, not in colour)
        grain = (np.random.default_rng(1).random((h, w, 1)) * 256).astype(np.uint8)
        self.grain = ui.to_surface(np.repeat(grain, 3, axis=2))
        self.grains = {}  # amplitude -> the pattern scaled to it
        self.collapse = float(render.look_settings(cfg)['spot_collapse'])
        self.flat = {}  # object id -> (picture, molding): the render x this
        # is the same light without its spot (render.spot_ratio)
        self.regions = []  # (object id, bounding rect, mask surface)
        for o in cfg['objects']:
            # projector pixels, like the renders the lamps get (keystone)
            mask = render.projector_mask(cfg, o['id']).astype(np.uint8) * 255
            mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
            ys, xs = np.nonzero(mask)
            if not len(xs):
                continue  # off the canvas
            rect = pygame.Rect(xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
            part = mask[rect.top : rect.bottom, rect.left : rect.right]
            self.regions.append(
                (o['id'], rect, ui.to_surface(np.repeat(part[:, :, None], 3, axis=2)))
            )
            gy, gx = np.mgrid[rect.top : rect.bottom, rect.left : rect.right]
            gx, gy = render.to_plane(gx.ravel(), gy.ravel(), cfg)  # where the spots are
            self.flat[o['id']] = tuple(
                ui.to_surface(
                    (
                        render.spot_ratio(cfg, o, gx, gy, on_molding=m).reshape(
                            rect.height, rect.width, 3
                        )
                        * 255.0
                        + 0.5
                    ).astype(np.uint8)
                )
                for m in (False, True)
            )

    def _grain(self, fills):
        """(noise pattern, the half to take off again so it does not
        brighten) for a multiplier of `fills`, or None when the steps are
        small enough to need no dither."""
        amp = grain_amplitude(fills)
        if max(amp) < 2:
            return None
        if amp not in self.grains:
            if len(self.grains) > 4:
                self.grains.clear()
            scaled = self.grain.copy()
            scaled.fill(amp, special_flags=pygame.BLEND_RGB_MULT)
            # the noise only adds, so half of it comes off again after:
            # dither that brightens is dither you can see in a slow fade
            self.grains[amp] = (scaled, tuple(a // 2 for a in amp))
        return self.grains[amp]

    def _collapse(self, part, src, rect, flat, level):
        """Flatten the pool in `part` (a copy of `src` over `rect`) as the
        lamp dims: mix in the same light without its spot, `flat` being
        what takes the spot out. Nothing to do at full level."""
        share = 1.0 - self.collapse * (1.0 - min(max(level, 0.0), 1.0))
        if share >= 1.0:
            return
        part.blit(flat, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        src.set_alpha(int(round(share * 255)))
        part.blit(src, (0, 0), area=rect)
        src.set_alpha(None)

    def _add(self, dst, part, where, fills):
        """Add `part` to `dst` at `where`, multiplied by `fills` (one blit,
        or two when the multiplier is above 1)."""
        first, second = fills
        if second is not None:
            extra = part.copy()
            extra.fill(second, special_flags=pygame.BLEND_RGB_MULT)
        part.fill(first, special_flags=pygame.BLEND_RGB_MULT)
        dst.blit(part, where, special_flags=pygame.BLEND_RGB_ADD)
        if second is not None:
            dst.blit(extra, where, special_flags=pygame.BLEND_RGB_ADD)

    def light(self, dst, live, molding, levels, dither=True):
        """Draw `live` (the stage as rendered, videos composited) onto
        `dst` under the lamps at `levels`; `molding` is the stage's
        molding-only render. `dither` off for an all-black stage (a
        blackout), whose canvas would otherwise pick up the grain."""
        if self.plain and levels.is_full(self.white):
            dst.blit(live, (0, 0))
            return
        dst.fill((0, 0, 0))
        for oid, rect, mask in self.regions:
            canvas, mold, kelvin = levels.objects.get(oid, (1.0, 1.0, self.white))
            if canvas > 0:
                fills = dim_fills(canvas, kelvin, self.white, self.amp)
                part = live.subsurface(rect).copy()
                self._collapse(part, live, rect, self.flat[oid][0], canvas)
                part.blit(molding, (0, 0), area=rect, special_flags=pygame.BLEND_RGB_SUB)
                # the grain goes on the picture before the mask cuts it out,
                # so it never lands outside the frame; the molding is a
                # narrow shaded band and needs none
                grain = self._grain(fills) if dither else None
                if grain is not None:
                    part.blit(grain[0], (0, 0), area=rect, special_flags=pygame.BLEND_RGB_ADD)
                    part.fill(grain[1], special_flags=pygame.BLEND_RGB_SUB)
                part.blit(mask, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
                self._add(dst, part, rect.topleft, fills)
            if mold > 0:
                part = molding.subsurface(rect).copy()
                self._collapse(part, molding, rect, self.flat[oid][1], mold)
                part.blit(mask, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
                self._add(dst, part, rect.topleft, dim_fills(mold, kelvin, self.white, self.amp))
