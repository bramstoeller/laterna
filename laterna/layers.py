"""A scene as objects, for the pptx (laterna/slides.py): what the renderer
composes into one picture, in layers one can move, swap or animate, lit as
the lamps light them with the desk at full (export.full_light), on the
plane (no keystone). Back to front:

  photo  a picture (or a video's first frame) whole, at its own size, lit
         where it lands on the canvas; the slide shows the part the frame
         shows through PowerPoint's own crop (and flip), so it can be
         re-cropped or swapped there. What the mapping's crop takes beyond
         the picture is in the file mirrored, as the show mirrors it
  plate  a flat colour over a frame's opening, lit there: the fill of a
         frame that shows nothing, a text's background
  text   a text's lines and parts as text boxes in the text's font
         (laterna/text.py, boxes)
  mask   one transparent PNG over the whole canvas: the molding, the
         frames' shadow on what is in them (black, half transparent),
         black everywhere else, the openings transparent

A blackout has no objects. Every object can come in later (`fade`): a
slideshow's pictures and a text's steps at their time.
"""

import cv2
import numpy as np
from PIL import ImageFont

from . import render, text

JPEG_QUALITY = 92


def _png(image):
    code = cv2.COLOR_RGBA2BGRA if image.shape[2] == 4 else cv2.COLOR_RGB2BGR
    ok, buf = cv2.imencode('.png', cv2.cvtColor(image, code))
    return buf.tobytes()


def _jpeg(rgb):
    ok, buf = cv2.imencode(
        '.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    )
    return buf.tobytes()


def step_times(mapping, count):
    """[(delay, duration)] of a slideshow's pictures 1..count-1 (the first
    is there from the start): picture k starts to fade in at hold + (k-1)
    x (hold + transition_time) after the scene is in, as the show plays it
    (SlideshowClip); a looping one goes round once."""
    hold = float(mapping.get('hold', render.SLIDESHOW_DEFAULTS['hold']))
    tt = float(mapping.get('transition_time', render.SLIDESHOW_DEFAULTS['transition_time']))
    return [(hold + (k - 1) * (hold + tt), tt) for k in range(1, count)]


class Layers:
    def __init__(self, cfg, renderer, gain):
        """renderer: a render.SceneRenderer of cfg (its supersampling and
        molding); gain: the lamps' gain at full light (export.full_light)."""
        self.cfg = cfg
        self.r = renderer
        self.ss = renderer.ss
        self.gain = gain
        self.lut = render.gamma_lut(cfg)
        self.size = tuple(cfg['canvas'])
        self.objects = {o['id']: o for o in cfg['objects']}
        look = render.look_settings(cfg)
        self.images = float(look['images']) / render.headroom(cfg)
        self.fill = float(look['fill']) / render.headroom(cfg)
        # the frame shadow darkens the render before the gamma LUT; as
        # black over the lit picture it needs this exponent on the fade
        self.exponent = 1.0
        if self.lut is not None:
            values = np.ravel(np.asarray(cfg['gamma'], np.float64))
            self.exponent = float(np.mean(render.GAMMA_TARGET / values))
        self.masks = {}  # molding colour -> PNG bytes
        # canvas px on each object's wood
        self.members = {o['id']: render.object_mask(cfg, o['id']) for o in cfg['objects']}

    # --- light -----------------------------------------------------------

    def _lit(self, colour):
        """Float colour (0..255, as the renderer composes it) -> uint8 as
        the projector shows it at full: rounded, the gamma LUT, the lamps'
        gain."""
        out = np.clip(colour + 0.5, 0, 255).astype(np.uint8)
        out = render.apply_gamma(out, self.lut)
        return np.clip(out.astype(np.float32) * self.gain, 0, 255).astype(np.uint8)

    def _gain(self, ids, xs, ys, spot=True, fill=False):
        """Brightness x spot (n, 3) at canvas px (xs, ys): each point lit by
        the spot of the object it lies on (the last of `ids` where it lies
        on none of them: under the mask)."""
        out = np.full((len(xs), 3), self.fill if fill else self.images, np.float32)
        if not spot:
            return out
        w, h = self.size
        cx = np.clip(xs.astype(int), 0, w - 1)
        cy = np.clip(ys.astype(int), 0, h - 1)
        todo = np.ones(len(xs), bool)
        for k, oid in enumerate(ids):
            sel = todo & self.members[oid][cy, cx] if k + 1 < len(ids) else todo
            if not sel.any():
                continue
            pool = render._spot_gain(
                self.cfg,
                self.objects[oid],
                xs[sel] * self.ss,
                ys[sel] * self.ss,
                self.ss,
                on_images=not fill,
                on_fill=fill,
            )
            if pool is not None:
                out[sel] *= pool
            todo &= ~sel
        return out

    # --- the objects -----------------------------------------------------

    def photo(self, mapping, source):
        """A picture as `mapping` shows it: {'kind': 'pic', 'data' (JPEG),
        'box' (x, y, w, h canvas px), 'crop' (l, t, r, b: the fractions of
        the file PowerPoint crops away), 'flip'}. `source`: the RGB float
        picture as read (render._load_image), uncropped."""
        h, w = source.shape[:2]
        crop = mapping.get('crop')
        if crop is None:
            cx0, cy0, cx1, cy1 = 0, 0, w, h
        else:  # as render.crop_medium rounds them
            cx0, cx1 = (round(float(v) * w) for v in (crop[0], crop[2]))
            cy0, cy1 = (round(float(v) * h) for v in (crop[1], crop[3]))
            cx1, cy1 = max(cx1, cx0 + 1), max(cy1, cy0 + 1)
        pad = [max(0, -cy0), max(0, cy1 - h), max(0, -cx0), max(0, cx1 - w)]
        pad = [min(v, (h if k < 2 else w) - 1) for k, v in enumerate(pad)]
        if any(pad):
            source = cv2.copyMakeBorder(source, *pad, cv2.BORDER_REFLECT_101)
        cx0, cx1, cy0, cy1 = cx0 + pad[2], cx1 + pad[2], cy0 + pad[0], cy1 + pad[0]
        ph, pw = source.shape[:2]
        cw, ch = cx1 - cx0, cy1 - cy0
        flip = bool(mapping.get('flip'))
        # where the cropped picture goes, in supersampled px (render.draw_source)
        mmpp = float(self.cfg['scale_mm_per_px']) / self.ss
        ref = self.r._rect(mapping.get('fit', mapping['objects']))
        x0, y0, x1, y1 = render.mapping_rect(mapping, ref, (cw, ch), mmpp)
        bw, bh = x1 - x0, y1 - y0
        if render.sized_explicitly(mapping):
            sx, sy, ox, oy = bw / cw, bh / ch, 0, 0
        else:  # render._cover
            s = max(bw / cw, bh / ch)
            nw, nh = max(bw, round(cw * s)), max(bh, round(ch * s))
            sx, sy, ox, oy = nw / cw, nh / ch, (nw - bw) // 2, (nh - bh) // 2
        # the part shown, in the cropped picture's px, then in the file's
        u0, u1 = ox / sx, (ox + bw) / sx
        v0, v1 = oy / sy, (oy + bh) / sy
        if flip:
            u0, u1 = cw - u1, cw - u0
        crop_out = (
            (cx0 + u0) / pw,
            (cy0 + v0) / ph,
            1 - (cx0 + u1) / pw,
            1 - (cy0 + v1) / ph,
        )
        # every pixel of the file lit where it lands on the canvas
        us = np.arange(pw, dtype=np.float64) + 0.5 - cx0
        vs = np.arange(ph, dtype=np.float64) + 0.5 - cy0
        xs = (x0 - ox + ((cw - us) if flip else us) * sx) / self.ss
        ys = (y0 - oy + vs * sy) / self.ss
        gx, gy = np.meshgrid(xs, ys)
        gain = self._gain(mapping['objects'], gx.ravel(), gy.ravel(), mapping.get('spot', True))
        lit = self._lit(source * gain.reshape(ph, pw, 3))
        return {
            'kind': 'pic',
            'data': _jpeg(lit),
            'ext': 'jpg',
            'box': (x0 / self.ss, y0 / self.ss, bw / self.ss, bh / self.ss),
            'crop': crop_out,
            'flip': flip,
        }

    def plate(self, oid, rgb, spot=True, fill=False):
        """A flat colour over object oid's opening, lit there (the fill:
        look.fill and spot.fill; else as a picture): {'kind': 'pic', ...}."""
        x0, y0, x1, y1 = self._box(self.r.image_polys[oid])
        ys, xs = np.mgrid[y0:y1, x0:x1]
        gain = self._gain([oid], xs.ravel() + 0.5, ys.ravel() + 0.5, spot, fill)
        colour = np.asarray(rgb, np.float32) * gain.reshape(y1 - y0, x1 - x0, 3)
        return {
            'kind': 'pic',
            'data': _png(self._lit(colour)),
            'ext': 'png',
            'box': (x0, y0, x1 - x0, y1 - y0),
            'crop': None,
            'flip': False,
        }

    def _box(self, polys):
        """(x0, y0, x1, y1) int canvas px around supersampled polygons, x1
        and y1 past the pixel their edge is in (a polygon is filled up to
        its edge)."""
        x0, y0, x1, y1 = render._bbox(polys)
        w, h = self.size
        return (
            max(0, int(np.floor(x0 / self.ss))),
            max(0, int(np.floor(y0 / self.ss))),
            min(w, int(np.floor(x1 / self.ss)) + 1),
            min(h, int(np.floor(y1 / self.ss)) + 1),
        )

    def mask(self, scene):
        """The mask of a lit scene (see the module doc), the same PNG for
        every scene with the same molding colour."""
        key = tuple(scene.get('molding_color') or ())
        if key not in self.masks:
            r = self.r
            hs, ws = r.shape
            alpha = np.ones(hs * ws, np.float32)
            opening = render._fill_mask(r.shape, [p for o in r.image_polys.values() for p in o])
            opening = opening.reshape(-1)
            alpha[opening] = 0.0
            inside = opening[r.fade_idx]
            alpha[r.fade_idx[inside]] = 1.0 - r.fade[inside] ** self.exponent
            colour = np.zeros((hs * ws, 3), np.float32)
            colour[r.molding_idx] = r.molding_colors(scene.get('molding_color'))
            alpha[r.molding_idx] = 1.0
            alpha = alpha.reshape(hs, ws)
            premult = colour.reshape(hs, ws, 3) * alpha[..., None]
            small = cv2.resize(alpha, self.size, interpolation=cv2.INTER_AREA)
            premult = cv2.resize(premult, self.size, interpolation=cv2.INTER_AREA)
            straight = premult / np.maximum(small, 1e-6)[..., None] + r.dither - 0.5
            a = np.clip(small * 255.0 + 0.5, 0, 255).astype(np.uint8)
            self.masks[key] = _png(np.dstack([self._lit(straight), a]))
        w, h = self.size
        return {
            'kind': 'pic',
            'data': self.masks[key],
            'ext': 'png',
            'box': (0, 0, w, h),
            'crop': None,
            'flip': False,
        }

    def texts(self, mapping):
        """The text mapping's parts (text.boxes) as text boxes on the
        canvas: {'kind': 'text', 'text', 'x', 'baseline', 'width', 'size',
        'ascent', 'descent' (canvas px), 'align', 'italic', 'bold',
        'color' (lit), 'font' (its family name), 'step'}."""
        setting = mapping['_text']
        oid = mapping['objects'][0]
        (ih, iw), parts = text.boxes(self.cfg, setting)
        mmpp = float(self.cfg['scale_mm_per_px']) / self.ss
        x0, y0, x1, y1 = render.mapping_rect(mapping, self.r._rect([oid]), (iw, ih), mmpp)
        bw, bh = x1 - x0, y1 - y0
        s = max(bw / iw, bh / ih)  # the pictures are cover-fitted (render._cover)
        nw, nh = max(bw, round(iw * s)), max(bh, round(ih * s))
        sx, sy = nw / iw / self.ss, nh / ih / self.ss
        ox, oy = (x0 - (nw - bw) // 2) / self.ss, (y0 - (nh - bh) // 2) / self.ss
        family = ImageFont.truetype(str(self.cfg['_dir'] / setting['font']), 12).getname()[0]
        color = np.asarray(setting.get('color') or (255, 255, 255), np.float32)
        spot = mapping.get('spot', True)
        out = []
        for p in parts:
            q = dict(p, kind='text', font=family)
            q['x'] = ox + p['x'] * sx
            q['baseline'] = oy + p['baseline'] * sy
            for k in ('width', 'size', 'ascent', 'descent'):
                q[k] = p[k] * sy
            at = np.array([q['x'] + q['width'] / 2]), np.array([q['baseline'] - q['ascent'] / 2])
            lit = self._lit((color * self._gain([oid], *at, spot)[0]).reshape(1, 1, 3))
            q['color'] = tuple(int(v) for v in lit.reshape(3))
            out.append(q)
        return out

    def scene(self, scene, source):
        """The objects of a scene, back to front, each with a 'name' and
        'fade' (None: there from the start, or (delay, duration) in seconds
        after the scene is in). source(path): the RGB float picture as read,
        or a video's first frame (None: missing)."""
        if scene.get('blackout'):
            return []
        out = []
        mapped = set()
        for m in scene.get('mappings', []):
            mapped.update(m['objects'])
            if '_text' in m:
                setting = m['_text']
                back = setting.get('background') or (0, 0, 0)
                plate = self.plate(m['objects'][0], back, m.get('spot', True))
                out.append(dict(plate, fade=None, name='text background'))
                parts = self.texts(m)
                times = step_times(m, len(parts) + 1) if 'slideshow' in m else []
                for p in parts:
                    fade = times[p['step'] - 1] if times else None
                    out.append(dict(p, fade=fade, name=f'text {p["step"]}'))
                continue
            paths = m['slideshow'] if 'slideshow' in m else [m.get('image') or m.get('video')]
            for path, fade in zip(paths, [None] + step_times(m, len(paths))):
                picture = source(path)
                if picture is None:
                    continue
                shown = m
                if render.is_video_path(path):  # crop and flip are a picture's
                    shown = {k: v for k, v in m.items() if k not in ('crop', 'flip')}
                pic = self.photo(shown, picture)
                out.append(dict(pic, fade=fade, name=str(path).split('/')[-1]))
        fill = scene.get('fill_color')
        plates = []
        for oid, fills in self.r.fill_base.items():
            if oid not in mapped:
                rgb = fill if fill is not None else (fills[0][1] if fills else (0, 0, 0))
                plates.append(dict(self.plate(oid, rgb, fill=True), fade=None, name='fill'))
        return plates + out + [dict(self.mask(scene), fade=None, name='frames')]
