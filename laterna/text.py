"""Text in a frame: a scenes.yaml mapping with `text:` instead of a picture.

  - text:
      - "Een leven in puin, net als dat van ons"
      - "In plaats van de soeverein te spelen"
      - ""                                    # a stanza break
      - "Iedereen staat op de rand van de afgrond"
      - {text: "– Jep Gambardella", align: right}
      - {text: "La Grande Bellezza", align: right, italic: true}
      - {text: "1926", x: -900, y: 150}       # a place of its own
    font: fonts/Attic.ttf     # a .ttf / .otf, relative to the show folder
    color: [255, 255, 255]    # null: white
    background: [0, 0, 0]     # null: black
    align: left               # of the lines in the block; null: left
    valign: bottom            # of the block in the opening; null: bottom
    size: auto                # font size in mm; auto (or null): as large as fits
    line_height: auto         # in font sizes; auto (or null): spread to fit
    margin: 0.05              # or [top, right, bottom, left]; null: 0.05
    reveal: true              # line by line: every hold + transition_time a line
    hold: 2
    transition_time: 1
    objects: [2]
    spot: false

Every option may be left out or set to null (or auto, where it says so)
for its default. A line is a string or {text, align, italic, bold, scale,
x, y}: `align` left, center or right within the text block (default the
mapping's), `italic` slants it (as PowerPoint does for a font without an
italic), `bold` thickens it, `scale` sizes it relative to the others; an
empty line is a stanza break (STANZA of a line). A line with `x` and `y`
(mm, in the frame's own measure: x from its middle, y up from the bottom
of the wood, as config.yaml's frame corners) stands there instead of in
the block: x is its left edge, middle or right edge as it aligns, y its
baseline.

The block of the other lines is centred between the left and right
margins and stands on the bottom margin (`valign: bottom`, an arch is
widest there), under the top one (top) or in the middle between them.
`margin` is a fraction of the opening's width, one for all sides or four.
`size: auto` takes the largest size at which everything fits inside the
opening less the margins, the lines with x and y included; a fixed size
that does not fit is an error. With an automatic line height the lines
spread out (up to SPREAD_MAX) when the width limits the size and height
is left; a number is the pitch in font sizes (LEAD by default).

Text goes in one frame (one object, standing straight, no fit or size).
It is set into pictures the size of the opening (PX_PER_MM), kept in
_text/ in the show folder under a hash of everything they depend on (old
ones there can go: they are made again when needed),
and the mapping becomes an `image:` (or with reveal a `slideshow:` with
loop false: an empty picture first, then one more line each step, in the
order of the list), so playing, Look and the export treat it as any
picture.
"""

import hashlib
import json
import os
import pathlib

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TEXT_DIR = '_text'
PX_PER_MM = 0.9  # the pictures' resolution in the opening
LEAD = 1.05  # line pitch, in font sizes
STANZA = 0.45  # an empty line, in font sizes
SPREAD_MAX = 1.6  # the automatic line height grows at most this much
MARGIN = 0.05  # the default margin, of the opening's width
SLANT = 0.2  # italic: the shear
BOLD = 0.03  # bold: the stroke, in font sizes
VERSION = 2  # of the layout: part of the hash (raise it when the layout changes)
KEYS = (
    'text',
    'font',
    'color',
    'background',
    'align',
    'valign',
    'size',
    'line_height',
    'margin',
    'reveal',
)


def lines_of(mapping):
    """The lines as dicts {text, align, italic, bold, scale, x, y}."""
    out = []
    for line in mapping['text']:
        line = {'text': line} if isinstance(line, str) else dict(line)
        out.append(
            {
                'text': line['text'],
                'align': line.get('align') or mapping.get('align') or 'left',
                'italic': bool(line.get('italic')),
                'bold': bool(line.get('bold')),
                'scale': float(line.get('scale') or 1.0),
                'x': line.get('x'),
                'y': line.get('y'),
            }
        )
    return out


def opening(cfg, oid):
    """(the opening of object `oid` as a mask at PX_PER_MM, the size of the
    inner outline's bounding box as the pictures are fitted; to_px(x, y):
    a point in the frame's mm, x from its middle, y up from the bottom of
    the wood, as picture px)."""
    obj = next(o for o in cfg['objects'] if o['id'] == oid)
    if float(obj.get('rotation') or 0.0):
        raise ValueError(
            f'text: object {oid} is rotated, text goes in a frame that stands straight'
        )
    scale = float(obj.get('scale') or 1.0)
    inner = np.array(obj['frame']['inner'], float) * scale
    lo, hi = inner.min(axis=0), inner.max(axis=0)
    w = max(1, round((hi[0] - lo[0]) * PX_PER_MM))
    h = max(1, round((hi[1] - lo[1]) * PX_PER_MM))
    px = np.column_stack(
        [(inner[:, 0] - lo[0]) / (hi[0] - lo[0]) * w, (hi[1] - inner[:, 1]) / (hi[1] - lo[1]) * h]
    )
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.round(px).astype(np.int32)], 255)

    def to_px(x, y):
        return (
            (float(x) * scale - lo[0]) / (hi[0] - lo[0]) * w,
            (hi[1] - float(y) * scale) / (hi[1] - lo[1]) * h,
        )

    return mask, to_px


class Layout:
    """The text set at one font size and line spread: the flowing lines as
    a block (placed by _place()), the lines with x and y where they say."""

    def __init__(self, font_path, lines, size, spread, lead):
        self.entries = []  # per non-empty line, in order: see below
        fonts = {}
        y = 0.0
        width = 0.0
        for line in lines:
            fixed = line['x'] is not None
            if not line['text'].strip():
                if not fixed:
                    y += STANZA * size * spread
                continue
            px = max(1, round(size * line['scale']))
            if px not in fonts:
                fonts[px] = ImageFont.truetype(str(font_path), px)
            font = fonts[px]
            layer, advance = self._line(line, font)
            entry = {'line': line, 'layer': layer, 'advance': advance, 'pad': font.size}
            entry['ascent'] = font.getmetrics()[0]
            entry['fixed'] = fixed
            if not fixed:
                entry['y'] = y
                width = max(width, advance)
                y += lead * px * spread
            self.entries.append(entry)
        self.width, self.height = width, y
        flowing = [e for e in self.entries if not e['fixed']]
        self.pad = max((e['pad'] for e in flowing), default=0)
        for e in flowing:
            e['x'] = _anchor(e['line']['align'], width, e['advance'])

    @staticmethod
    def _line(line, font):
        """(the line's ink as an L image with a pad of one font size, its advance)."""
        pad = font.size
        advance = font.getlength(line['text'])
        stroke = round(BOLD * font.size) if line['bold'] else 0
        layer = Image.new('L', (int(advance) + 2 * pad, int(font.size * 1.6) + 2 * pad), 0)
        ImageDraw.Draw(layer).text(
            (pad, pad), line['text'], 255, font, stroke_width=stroke, stroke_fill=255
        )
        if line['italic']:  # shear about the baseline
            base = pad + font.getmetrics()[0]
            layer = layer.transform(
                layer.size, Image.AFFINE, (1, SLANT, -SLANT * base, 0, 1, 0), Image.BICUBIC
            )
        return layer, advance

    def ink(self, upto=None):
        """The flowing lines among the first `upto` (all: None) as a mask,
        the block's top left at (self.pad, self.pad)."""
        m = self.pad
        size = (int(self.width) + 4 * m + 1, int(self.height) + 4 * m + 1)
        image = Image.new('L', size, 0)
        for e in self.entries[:upto]:
            if not e['fixed']:
                image.paste(
                    255, (round(e['x']) + m - e['pad'], round(e['y']) + m - e['pad']), e['layer']
                )
        return np.array(image)

    def fixed(self, shape, to_px, upto=None):
        """(the lines with x and y among the first `upto` in a picture of
        `shape`, the ink that fell outside it): x is the line's left edge,
        middle or right edge as it aligns, y its baseline."""
        h, w = shape
        m = max((e['pad'] for e in self.entries if e['fixed']), default=0) * 2
        image = Image.new('L', (w + 2 * m, h + 2 * m), 0)
        total = 0  # the lines' ink, wherever it lands
        for e in self.entries[:upto]:
            if e['fixed']:
                x, y = to_px(e['line']['x'], e['line']['y'])
                left = x + _anchor(e['line']['align'], 0.0, e['advance'])
                top = y - e['ascent']
                image.paste(
                    255, (round(left) + m - e['pad'], round(top) + m - e['pad']), e['layer']
                )
                total += int(np.count_nonzero(np.array(e['layer'])))
        inside = np.ascontiguousarray(np.array(image)[m : m + h, m : m + w])
        return inside, total - np.count_nonzero(inside)


def _anchor(align, width, advance):
    """Where a line of `advance` starts in a block of `width` (0: its anchor)."""
    return {'left': 0.0, 'center': (width - advance) / 2, 'right': width - advance}[align]


def margins(mapping, width):
    """The margins (top, right, bottom, left) in px: `margin`, one fraction
    of the opening's width or four, [t, r, b, l]; default 0.05 all round."""
    value = mapping.get('margin')
    value = MARGIN if value in (None, 'auto') else value
    values = value if isinstance(value, list) else [value] * 4
    return tuple(round(float(v) * width) for v in values)


def _place(ink, shape, valign, margin):
    """The block's ink moved into an image of `shape`: centred between the
    left and right margins, `valign` on the top or bottom margin (or in the
    middle between them); also returns the shift, to place partial texts
    the same way."""
    h, w = shape
    top, right, bottom, left = margin
    ys, xs = np.where(ink > 0)
    if not len(ys):
        return np.zeros(shape, np.uint8), (0, 0)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    dx = left + round(((w - left - right) - (x1 - x0 + 1)) / 2) - x0
    if valign == 'top':
        dy = top - y0
    elif valign == 'middle':
        dy = top + round(((h - top - bottom) - (y1 - y0 + 1)) / 2) - y0
    else:
        dy = (h - bottom - 1) - y1
    return _shift(ink, shape, dx, dy), (dx, dy)


def _shift(ink, shape, dx, dy):
    h, w = shape
    out = np.zeros(shape, np.uint8)
    ys, xs = np.where(ink > 0)
    keep = (ys + dy >= 0) & (ys + dy < h) & (xs + dx >= 0) & (xs + dx < w)
    out[ys[keep] + dy, xs[keep] + dx] = ink[ys[keep], xs[keep]]
    return out


def _fits(ink, allowed):
    return not ((ink > 0) & (allowed == 0)).any()


def _allowed(mask, margin):
    """The opening less the margins (t, r, b, l): where ink may go."""
    top, right, bottom, left = margin
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (left + right + 1, top + bottom + 1))
    return cv2.erode(mask, kernel, anchor=(left, top))


def compose(lay, shape, to_px, shift, upto=None):
    """The picture's ink: the flowing block moved by `shift`, and the lines
    with x and y."""
    fixed, _ = lay.fixed(shape, to_px, upto)
    return np.maximum(_shift(lay.ink(upto), shape, *shift), fixed)


def layout(mapping, font_path, mask, to_px):
    """(the Layout, the block's shift) for the mapping in the opening
    `mask`: the fixed size, or (`size: auto` or none) the largest at which
    everything fits, then, with an automatic line height, spread as far as
    it still fits, up to SPREAD_MAX."""
    lines = lines_of(mapping)
    shape = mask.shape
    margin = margins(mapping, shape[1])
    top, right, bottom, left = margin
    allowed = _allowed(mask, margin)
    valign = mapping.get('valign') or 'bottom'
    automatic = mapping.get('line_height') in (None, 'auto')
    lead = LEAD if automatic else float(mapping['line_height'])

    def attempt(size, spread):
        lay = Layout(font_path, lines, size, spread, lead)
        if lay.width > shape[1] - left - right or lay.height > shape[0] - top - bottom:
            return lay, (0, 0), False  # the block is too large already
        ink = lay.ink()
        placed, shift = _place(ink, shape, valign, margin)
        fixed, lost = lay.fixed(shape, to_px)
        # all ink landed (none cut off at the picture's edge) and inside the margins
        fits = (
            np.count_nonzero(placed) == np.count_nonzero(ink)
            and not lost
            and _fits(np.maximum(placed, fixed), allowed)
        )
        return lay, shift, fits

    size = mapping.get('size')
    if size not in (None, 'auto'):
        lay, shift, fits = attempt(float(size) * PX_PER_MM, 1.0)
        if not fits:
            raise ValueError(
                f'text: size {size:g} mm does not fit the opening (size: auto takes the largest '
                'that fits)'
            )
        return lay, shift
    lo, hi = 4, max(8, shape[0])  # font px: lo fits, hi does not
    if not attempt(lo, 1.0)[2]:
        raise ValueError('text: does not fit the opening even at the smallest size')
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if attempt(mid, 1.0)[2] else (lo, mid)
    best = attempt(lo, 1.0)
    if automatic:
        s_lo, s_hi = 1.0, SPREAD_MAX
        if attempt(lo, s_hi)[2]:
            s_lo = s_hi
        else:
            for _ in range(12):
                mid = (s_lo + s_hi) / 2
                s_lo, s_hi = (mid, s_hi) if attempt(lo, mid)[2] else (s_lo, mid)
        best = attempt(lo, s_lo)
    return best[0], best[1]


def _key(mapping, font_path, mask):
    h = hashlib.sha256()
    h.update(json.dumps({k: mapping.get(k) for k in KEYS}, sort_keys=True, default=str).encode())
    h.update(pathlib.Path(font_path).read_bytes())
    h.update(np.ascontiguousarray(mask).tobytes())
    h.update(str((VERSION, PX_PER_MM, LEAD, STANZA, SPREAD_MAX, SLANT, BOLD)).encode())
    return h.hexdigest()[:16]


def _picture(ink, mapping):
    """RGB: the ink in `color` over `background`."""
    color = np.array(mapping.get('color') or (255, 255, 255), np.float32)
    back = np.array(mapping.get('background') or (0, 0, 0), np.float32)
    a = (ink.astype(np.float32) / 255.0)[..., None]
    return np.round(back * (1 - a) + color * a).astype(np.uint8)


def _write(path, rgb):
    """The picture as a PNG, whole or not at all: written next to it and
    then renamed (a file that exists counts as done); by bytes, so a path
    with any characters works on Windows too."""
    ok, png = cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise OSError(f'could not encode {path.name}')
    part = path.with_name(path.name + '.part')
    part.write_bytes(png.tobytes())
    os.replace(part, path)


def render_mapping(cfg, mapping):
    """The mapping with its text set: `image:` (or `slideshow:`), the files
    in _text/ (made when missing), relative to the show folder."""
    folder = pathlib.Path(cfg['_dir'])
    font_path = folder / mapping['font']
    if not font_path.exists():
        raise ValueError(f'text: font {mapping["font"]} not found in the show folder')
    mask, to_px = opening(cfg, mapping['objects'][0])
    key = _key(mapping, font_path, mask)
    count = sum(1 for line in lines_of(mapping) if line['text'].strip())
    reveal = bool(mapping.get('reveal'))
    names = (
        [f'{TEXT_DIR}/{key}-{k:02d}.png' for k in range(count + 1)]
        if reveal
        else [f'{TEXT_DIR}/{key}.png']
    )
    if not all((folder / n).exists() for n in names):
        lay, shift = layout(mapping, font_path, mask, to_px)
        (folder / TEXT_DIR).mkdir(exist_ok=True)
        steps = range(count + 1) if reveal else [None]
        for name, upto in zip(names, steps):
            ink = compose(lay, mask.shape, to_px, shift, upto)
            _write(folder / name, _picture(ink, mapping))
    out = {k: v for k, v in mapping.items() if k not in KEYS}
    out['text'] = mapping['text']  # kept for the export's scenes sheet
    if reveal:
        out['slideshow'] = names
        out['loop'] = False
    else:
        out['image'] = names[0]
    return out
