"""Video playback inside stages: looping clips composited into the polygons.

A stage's static parts (molding, separator, mapped images, black) come from
render.StageRenderer as a premultiplied base plus an alpha layer (see
StageRenderer.video_alpha). Per video frame only the pixels inside the
mapping's own polygons change:

    out = frame * (1 - alpha) + base

The hard per-mapping pixel mask keeps a clip inside its own polygons even
where the bounding boxes of neighbouring mappings overlap; the soft
downscaled alpha does the anti-aliasing and the separator fade, and the
visible edge always lies under the molding, so the hard mask itself is
never seen.

Clips loop and follow the wall clock: the frame shown is the one belonging
to the elapsed time, and when decoding falls behind, frames are dropped —
playback never slows down.

A slideshow mapping (`slideshow: [files]`) goes through the same
compositing. Image slides are fitted, gamma-corrected and composited over
the polygons once, up front, so per tick only the blend of the two slides
of a running transition (`transition: fade`) is written into the canvas;
during a hold of an image nothing is redrawn. A video slide (recognised by
its extension) owns a VideoClip that starts when the slide fades in, plays
for the slide's hold (looping when shorter, cut when longer) and is
composited per frame like a stage video. The slideshow timeline
(SlideshowClip) lives in the same clip pool as the stage videos and loops
the same way.
"""

import cv2
import numpy as np

from . import render


class VideoClip:
    """Sequential decoder for one looping clip, synced to the wall clock."""

    def __init__(self, path):
        self.path = str(path)
        self.cap = cv2.VideoCapture(self.path)
        self.ok = self.cap.isOpened()
        if not self.ok:
            print(f'warning: {self.path} missing or unreadable, stays black')
        fps = self.cap.get(cv2.CAP_PROP_FPS) if self.ok else 0.0
        self.fps = fps if fps and fps > 0 else 25.0
        self.count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if self.ok else 0
        self.pos = -1  # index of the frame currently held
        self.frame = None  # last decoded frame (BGR)
        self.t0 = None  # wall-clock start of the timeline; None = stopped

    def next_change(self, t):
        """(False, seconds until the clip starts over, the clip's length)
        at t seconds in: a video has no fade, so it never reports one.
        None when there is nothing to count (unreadable, one frame)."""
        if not self.ok or self.count <= 1:
            return None
        length = self.count / self.fps
        return False, length - (t % length), length

    def rewind(self):
        if self.ok:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.pos = -1
        self.frame = None

    def frame_at(self, t):
        """The frame belonging to t seconds after the start, looping.

        Frames we are behind on are skipped with grab() (decode without the
        convert/copy of read()), or a seek when very far behind, so playback
        stays at normal speed. None when the clip is unreadable."""
        if not self.ok:
            return None
        target = int(t * self.fps)
        if target == self.pos:
            return self.frame
        if target < self.pos:  # timeline jumped back (a slideshow slot came round)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target % self.count if self.count > 1 else 0)
            self.pos = target - 1
        if self.count > 1 and target - self.pos > 5 * self.fps:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target % self.count)
            self.pos = target - 1
        while self.pos < target:
            if self.pos < target - 1:
                got, frame = self.cap.grab(), None
            else:
                got, frame = self.cap.read()
            if not got:  # end of the clip: loop
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                got, frame = self.cap.read()
                if not got:
                    print(f'warning: {self.path}: decode failed, video freezes')
                    self.ok = False
                    return self.frame
            self.pos += 1
            if frame is not None:
                self.frame = frame
        return self.frame


class SlideshowClip:
    """Timeline of a slideshow: which slide(s) are due at a moment.

    Offers the part of the VideoClip interface the player relies on (t0,
    rewind, pos), so a slideshow shares the clip pool with the videos and,
    like them, runs on seamlessly when the next stage maps the same show.
    Every slide holds `hold` seconds, then fades in `transition_time`
    seconds to the next; after the last one the show wraps to the first."""

    def __init__(self, key, count, hold, transition_time):
        self.path = key
        self.count = count
        self.hold = float(hold)
        self.transition_time = float(transition_time)
        self.slot = self.hold + self.transition_time
        self.pos = None  # the state last returned by state_at
        self.t0 = None  # wall-clock start of the timeline; None = stopped

    def rewind(self):
        self.pos = None

    def _phase(self, t):
        """(which slide's slot t falls in, seconds into that slot)."""
        cycle = t % (self.count * self.slot)
        k = min(int(cycle // self.slot), self.count - 1)
        return k, cycle - k * self.slot

    def state_at(self, t):
        """(slide a, slide b, blend) due at t seconds after the start: blend
        0..255 is the weight of slide b; during a hold a == b and blend 0."""
        if self.count == 1:
            state = (0, 0, 0)
        else:
            k, phase = self._phase(t)
            if phase < self.hold or self.transition_time <= 0:
                state = (k, k, 0)
            else:
                blend = (phase - self.hold) / self.transition_time
                state = (k, (k + 1) % self.count, min(255, int(blend * 255)))
        self.pos = state
        return state

    def next_change(self, t):
        """(a transition is running, seconds until it ends or starts, the
        whole stretch it is counting down) at t seconds into the timeline:
        what the slideshow's own row of the state bar shows (laterna/play.py).
        None when the show has one slide and nothing ever changes."""
        if self.count < 2 or self.slot <= 0:
            return None
        _, phase = self._phase(t)
        if phase < self.hold:
            return False, self.hold - phase, self.hold
        return True, self.slot - phase, self.transition_time

    def step(self, t, delta):
        """Where to continue the timeline when the operator steps a
        picture on or back from t seconds in.

        On (delta 1) starts the fade to the next picture right away, the
        same fade the show would run by itself. Back (delta -1) cuts to
        the previous picture: the timeline only fades forwards, so fading
        there would mean showing the one before it first."""
        k, phase = self._phase(t)
        if phase >= self.hold:
            k = (k + 1) % self.count  # a fade runs: the incoming one is the picture now
        if delta > 0:
            return (k * self.slot + self.hold) % (self.count * self.slot)
        return ((k - 1) % self.count) * self.slot

    def local_time(self, t, k):
        """Seconds slide k has been visible at t: 0 at the start of its
        fade-in (the transition out of slide k-1), wrapping with the show."""
        period = self.count * self.slot
        return (t - k * self.slot + self.transition_time) % period if period else t


def _slideshow_key(mapping):
    """Pool key of a slideshow: same images and timing = same timeline."""
    return 'slideshow:{}:{}:{}:{}'.format(
        mapping.get('hold', render.SLIDESHOW_DEFAULTS['hold']),
        mapping.get('transition', render.SLIDESHOW_DEFAULTS['transition']),
        mapping.get('transition_time', render.SLIDESHOW_DEFAULTS['transition_time']),
        '|'.join(mapping['slideshow']),
    )


def _load_slide(path):
    """RGB uint8 image, or None (with a warning) when missing."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        print(f'warning: {path} missing or unreadable, slide stays black')
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _probe_size(cfg, mapping):
    """(width, height) of the medium, for the aspect ratio of an explicit
    single-dimension size — of the first slide for a slideshow; a safe
    guess when the file is unreadable (it stays black then anyway)."""
    path = cfg['_dir'] / render.media_paths(mapping)[0]
    if 'video' in mapping or render.is_video_path(path):
        cap = cv2.VideoCapture(str(path))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return (w, h) if w > 0 and h > 0 else (1280, 720)
    img = _load_slide(path)
    return (img.shape[1], img.shape[0]) if img is not None else (1280, 720)


def _region_rows(region, spec):
    """The pixels of a fitted region that land in the spec's polygons, as
    (n, 3) rows in the order of spec['flat']: straight indices, or with a
    keystone bilinear samples at where each projector pixel falls in the
    plane (spec['remap'])."""
    if spec['remap'] is None:
        return region.reshape(-1, 3)[spec['local']]
    mx, my, n = spec['remap']
    rows = cv2.remap(region, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return rows.reshape(-1, 3)[:n]


REMAP_WIDTH = 1024  # cv2.remap takes maps of at most 32767 rows or columns


def _remap_block(mx, my):
    """(map x, map y, n): n sample positions folded into REMAP_WIDTH-wide
    float32 maps (padded with the last position), for _region_rows."""
    n = len(mx)
    rows = -(-n // REMAP_WIDTH)
    pad = rows * REMAP_WIDTH - n

    def fold(v):
        v = np.concatenate([np.asarray(v, np.float32), np.repeat(np.float32(v[-1]), pad)])
        return v.reshape(rows, REMAP_WIDTH)

    return fold(mx), fold(my), n


def _compose_rows(region, spec, lut):
    """Composite a fitted RGB region over the spec's polygons: lit by the
    look (gain), then gamma-corrected, then blended with the static base —
    the same order the stills get in render.py (gain before the LUT, so a
    video and a still of the same picture come out equally bright). Returns
    uint8 (n, 3) rows ready to be written through spec['flat']."""
    vals = _region_rows(region, spec).astype(np.float32)
    if spec['gain'] is not None:
        vals *= spec['gain']
    lit = np.clip(vals, 0.0, 255.0).astype(np.uint8)
    vals = render.apply_gamma(lit, lut).astype(np.float32)
    out = vals * spec['inv_alpha'] + spec['premult']
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


def _fit_region(frame_bgr, spec):
    """A decoded video frame as the RGB region of a spec (not yet lit or
    gamma-corrected: _compose_rows does both)."""
    bw, bh = spec['size']
    fit = render._resize if spec['explicit'] else render._cover
    return cv2.cvtColor(fit(frame_bgr, bw, bh), cv2.COLOR_BGR2RGB)


def _composite_slides(cfg, mapping, spec, lut):
    """The slides of a slideshow mapping. An image slide is composited
    once, here: uint8 (n, 3) rows (see _compose_rows), so a transition only
    blends finished arrays. A video slide is a dict with its own VideoClip
    plus the rows of the frame composited last (_slide_rows fills them
    per tick)."""
    bw, bh = spec['size']
    fit = render._resize if spec['explicit'] else render._cover
    black = np.zeros((bh, bw, 3), np.uint8)  # a fitted region of black
    slides = []
    for name in mapping['slideshow']:
        path = cfg['_dir'] / name
        if render.is_video_path(path):
            slides.append(
                {'clip': VideoClip(path), 'pos': None, 'rows': _compose_rows(black, spec, lut)}
            )
            continue
        img = _load_slide(path)
        region = black if img is None else fit(img, bw, bh)
        slides.append(_compose_rows(region, spec, lut))
    return slides


def _slide_rows(spec, k, local_t, lut):
    """(rows, key) of slide k at local_t seconds into its slot: the key
    changes whenever the rows do (None for a still image, the frame index
    for a video slide), for the player's change detection."""
    slide = spec['slides'][k]
    if not isinstance(slide, dict):
        return slide, None
    clip = slide['clip']
    frame = clip.frame_at(local_t)
    if frame is None:  # unreadable: stays black
        return slide['rows'], None
    if clip.pos != slide['pos']:
        slide['pos'] = clip.pos
        slide['rows'] = _compose_rows(_fit_region(frame, spec), spec, lut)
    return slide['rows'], clip.pos


def build_specs(cfg, stage, base, alpha):
    """Compositing data per video or slideshow mapping of a stage.

    Each clip covers its mapping rect (cover-fit over the bounding box of
    its polygons or its `fit:` list, or the exact size of an explicit
    width/height — the same rules images follow) but is written only
    through the pixel indices of the mapped polygons themselves, so
    neighbouring polygons inside the rect are never touched. The blend weights come from the alpha layer and
    the base doubles as the premultiplied static colour."""
    mappings = [m for m in stage.get('mappings', []) if render.is_animated(m)]
    if not mappings:
        return []
    if alpha is None:
        raise ValueError('stage maps videos or slideshows but has no alpha layer')
    lut = render.gamma_lut(cfg)  # slides get the same correction as frames
    h, w = base.shape[:2]
    inv_alpha = 1.0 - alpha.reshape(-1).astype(np.float32) / 255.0
    premult = base.reshape(-1, 3)
    specs = []
    mmpp = float(cfg['scale_mm_per_px'])
    for m in mappings:
        polys = render.image_polys_px(cfg, m['objects'])
        rect = render.canvas_rect_px(cfg, m.get('fit', m['objects']))
        x0, y0, x1, y1 = render.mapping_rect(m, rect, _probe_size(cfg, m), mmpp)
        # the pixels to write are projector pixels (base and alpha are); the
        # rect and the spots live in the plane: with a keystone each pixel
        # is looked up where it falls in the plane (identical without one)
        warped = [render.to_projector(p, cfg) for p in polys]
        ys, xs = np.nonzero(render._fill_mask((h, w), warped))
        px, py = render.to_plane(xs, ys, cfg)
        # the anti-aliased fill can light single pixels just outside the
        # polygon bounding box; images ignore those (outside the blit slice),
        # so drop them here too
        keep = (py >= y0) & (py < y1) & (px >= x0) & (px < x1)
        ys, xs, px, py = ys[keep], xs[keep], px[keep], py[keep]
        if not len(ys):
            print(f'warning: {render.media_paths(m)[0]}: polygons outside the canvas')
            continue
        flat = ys * w + xs
        keystoned = render.keystone(cfg) is not None
        if keystoned:  # the spots at the nearest plane pixel
            gx = np.clip(np.round(px), 0, w - 1).astype(np.intp)
            gy = np.clip(np.round(py), 0, h - 1).astype(np.intp)
            remap = _remap_block(px - x0, py - y0)
        else:
            gx, gy, remap = xs, ys, None
        gain = render.image_gain_at(cfg, m['objects'], gx, gy)
        spec = {
            'kind': 'slideshow' if 'slideshow' in m else 'video',
            'gain': gain,  # brightness + spot (look), None when 1 everywhere
            'size': (int(x1 - x0), int(y1 - y0)),
            'explicit': render.sized_explicitly(m),
            'local': None if keystoned else (ys - y0) * (x1 - x0) + (xs - x0),
            'remap': remap,  # keystone: where each pixel samples the region
            'flat': flat,
            'inv_alpha': inv_alpha[flat][:, None],
            'premult': premult[flat].astype(np.float32),
            'shown': -1,
            'objects': list(m['objects']),
        }
        if spec['kind'] == 'slideshow':
            spec['path'] = _slideshow_key(m)  # pool key, see SlideshowClip
            spec['slides'] = _composite_slides(cfg, m, spec, lut)
            spec['timing'] = (
                len(m['slideshow']),
                m.get('hold', render.SLIDESHOW_DEFAULTS['hold']),
                m.get('transition_time', render.SLIDESHOW_DEFAULTS['transition_time']),
            )
        else:
            spec['path'] = str(cfg['_dir'] / m['video'])
        specs.append(spec)
    return specs


def _new_clip(spec):
    if spec['kind'] == 'slideshow':
        return SlideshowClip(spec['path'], *spec['timing'])
    return VideoClip(spec['path'])


class StagePlayer:
    """Playback state of one stage: the static base plus, when the stage
    maps videos, a working canvas the due frames composite into.

    Clips (videos and slideshow timelines) come from a shared pool, and
    each clip carries its own timeline: a clip that is already playing in
    the outgoing stage keeps running seamlessly when the next stage maps
    the same file (or the same slideshow), instead of starting over."""

    def __init__(self, cfg, stage, base, alpha, pool=None):
        self.specs = build_specs(cfg, stage, base, alpha)
        self.lut = render.gamma_lut(cfg)  # video frames get the same correction
        self.base = base
        self.canvas = base.copy() if self.specs else base
        pool = pool if pool is not None else {}
        self.clips = {s['path']: pool.setdefault(s['path'], _new_clip(s)) for s in self.specs}

    @property
    def animated(self):
        return bool(self.specs)

    def paths(self):
        return set(self.clips)

    def hold_dark(self, dt, lit):
        """Hold back the clocks of the clips none of whose objects is in
        `lit` (the ids whose canvas gives light) by `dt` seconds: in the
        dark a slideshow does not move on and a video does not run, they
        carry on where they were once their canvas is lit again."""
        for path, clip in self.clips.items():
            if clip.t0 is None:
                continue
            if not any(o in lit for s in self.specs if s['path'] == path for o in s['objects']):
                clip.t0 += dt

    def stop(self, keep=()):
        """Stop this stage's clips so they restart on the next entry —
        except the ones in `keep`, which the next stage keeps playing."""
        for path, clip in self.clips.items():
            if path not in keep:
                clip.t0 = None

    def tick(self, now):
        """(frame, changed) for wall-clock time now (seconds)."""
        if not self.specs:
            return self.base, False
        changed = False
        for s in self.specs:
            clip = self.clips[s['path']]
            if clip.t0 is None:  # (re)start this clip's own timeline
                clip.t0 = now
                clip.rewind()
                for spec in self.specs:
                    if spec['path'] == s['path']:
                        spec['shown'] = -1
            t = now - clip.t0
            if s['kind'] == 'slideshow':
                a, b, blend = state = clip.state_at(t)
                rows_a, key_a = _slide_rows(s, a, clip.local_time(t, a), self.lut)
                if blend == 0:
                    rows, key_b = rows_a, None
                else:
                    rows_b, key_b = _slide_rows(s, b, clip.local_time(t, b), self.lut)
                    rows = None
                if (state, key_a, key_b) == s['shown']:
                    continue
                s['shown'] = (state, key_a, key_b)
                if rows is None:
                    rows = cv2.addWeighted(rows_a, 1.0 - blend / 255.0, rows_b, blend / 255.0, 0.0)
                self.canvas.reshape(-1, 3)[s['flat']] = rows
                changed = True
                continue
            frame = clip.frame_at(t)
            if frame is None or clip.pos == s['shown']:
                continue
            s['shown'] = clip.pos
            self.canvas.reshape(-1, 3)[s['flat']] = _compose_rows(
                _fit_region(frame, s), s, self.lut
            )
            changed = True
        return self.canvas, changed
