"""Text in a frame: a scenes.yaml mapping with `text:` instead of a picture.

  - text:
      - "Een leven in puin, net als dat van ons"
      - "In plaats van de soeverein te spelen"
      - ""                                    # a stanza break
      - "Iedereen staat op de rand van de afgrond"
      - {text: "– Jep Gambardella", align: right}
      - {text: "La Grande Bellezza", align: right, italic: true}
    font: fonts/Attic.ttf     # a .ttf / .otf, relative to the show folder
    reveal: true              # line by line: every hold + transition_time a line
    hold: 2
    transition_time: 1
    objects: [2]
    spot: false

A line is a string or {text, align, italic, bold, scale}: `align` left,
center or right within the text block (default the mapping's `align`,
left), `italic` slants it (as PowerPoint does for a font without an
italic), `bold` thickens it, `scale` sizes it relative to the others. An
empty line is a stanza break (STANZA of a line).

For the whole text: `color` and `background` ([r, g, b], default white
on black), `valign` top, middle or bottom in the opening (default
bottom: an arch is widest there), `size` the font size in mm (an error
when it does not fit; default: as large as fits inside the opening, less
`margin`, a fraction of its width, default 0.05), `line_height` in font sizes
(default automatic: LEAD, spread up to SPREAD_MAX when the width limits
the size and height is left). The block is centred across the opening.

Text goes in one frame (one object, standing straight, no fit or size).
It is set into pictures the size of the opening (PX_PER_MM), kept in
_text/ in the show folder under a hash of everything they depend on (old
ones there can go: they are made again when needed),
and the mapping becomes an `image:` (or with reveal a `slideshow:` with
loop false: an empty picture first, then one more line each step), so
playing, Look and the export treat it as any picture.
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
SLANT = 0.2  # italic: the shear
BOLD = 0.03  # bold: the stroke, in font sizes
VERSION = 1  # of the layout: part of the hash
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
    """The lines as dicts {text, align, italic, bold, scale}."""
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
            }
        )
    return out


def opening(cfg, oid):
    """The opening of object `oid` as a mask at PX_PER_MM (the size of the
    inner outline's bounding box, as the pictures are fitted)."""
    obj = next(o for o in cfg['objects'] if o['id'] == oid)
    if float(obj.get('rotation') or 0.0):
        raise ValueError(
            f'text: object {oid} is rotated, text goes in a frame that stands straight'
        )
    inner = np.array(obj['frame']['inner'], float) * float(obj.get('scale') or 1.0)
    lo, hi = inner.min(axis=0), inner.max(axis=0)
    w = max(1, round((hi[0] - lo[0]) * PX_PER_MM))
    h = max(1, round((hi[1] - lo[1]) * PX_PER_MM))
    px = np.column_stack(
        [(inner[:, 0] - lo[0]) / (hi[0] - lo[0]) * w, (hi[1] - inner[:, 1]) / (hi[1] - lo[1]) * h]
    )
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.round(px).astype(np.int32)], 255)
    return mask


class Layout:
    """The text set at one font size and line spread, as rows of ink."""

    def __init__(self, font_path, lines, size, spread, lead):
        self.rows = []  # (y, x, layer, pad) per non-empty line, top-down
        fonts = {}
        y = 0.0
        width = 0.0
        placed = []
        for line in lines:
            if not line['text'].strip():
                y += STANZA * size * spread
                continue
            px = max(1, round(size * line['scale']))
            if px not in fonts:
                fonts[px] = ImageFont.truetype(str(font_path), px)
            font = fonts[px]
            layer, advance = self._line(line, font)
            placed.append((y, line, layer, advance, font.size))
            width = max(width, advance)
            y += lead * px * spread
        self.width, self.height = width, y
        self.pad = max((pad for *_, pad in placed), default=0)
        for y, line, layer, advance, pad in placed:
            offset = {'left': 0.0, 'center': (width - advance) / 2, 'right': width - advance}
            self.rows.append((y, offset[line['align']], layer, pad))

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
        """The first `upto` lines (all: None) as a mask, the block's top
        left at (self.pad, self.pad); _place() moves it into the opening."""
        m = self.pad
        size = (int(self.width) + 4 * m + 1, int(self.height) + 4 * m + 1)
        image = Image.new('L', size, 0)
        for y, x, layer, pad in self.rows[:upto]:
            image.paste(255, (round(x) + m - pad, round(y) + m - pad), layer)
        return np.array(image)


def _place(ink, shape, valign, margin_px):
    """The block's ink moved into an image of `shape`: centred across,
    `valign` on the margin; also returns the shift, to place partial texts
    the same way."""
    h, w = shape
    ys, xs = np.where(ink > 0)
    if not len(ys):
        return np.zeros(shape, np.uint8), (0, 0)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    dx = round((w - (x1 - x0 + 1)) / 2) - x0
    if valign == 'top':
        dy = margin_px - y0
    elif valign == 'middle':
        dy = round((h - (y1 - y0 + 1)) / 2) - y0
    else:
        dy = (h - margin_px - 1) - y1
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


def _allowed(mask, margin_px):
    k = 2 * margin_px + 1
    return cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))


def layout(mapping, font_path, mask):
    """(the Layout, the shift) for the mapping in the opening `mask`: the
    fixed size, or the largest that fits (then, with an automatic line
    height, spread as far as it still fits, up to SPREAD_MAX)."""
    lines = lines_of(mapping)
    shape = mask.shape
    margin_px = round(float(mapping.get('margin', 0.05)) * shape[1])
    allowed = _allowed(mask, margin_px)
    valign = mapping.get('valign') or 'bottom'
    lead = float(mapping['line_height']) if mapping.get('line_height') else LEAD

    def attempt(size, spread):
        lay = Layout(font_path, lines, size, spread, lead)
        if lay.width > shape[1] - 2 * margin_px or lay.height > shape[0] - 2 * margin_px:
            return lay, (0, 0), False  # too large already: no need to draw it
        ink = lay.ink()
        placed, shift = _place(ink, shape, valign, margin_px)
        # all ink landed (none cut off at the picture's edge) and inside the margin
        fits = np.count_nonzero(placed) == np.count_nonzero(ink) and _fits(placed, allowed)
        return lay, shift, fits

    if mapping.get('size'):
        lay, shift, fits = attempt(float(mapping['size']) * PX_PER_MM, 1.0)
        if not fits:
            raise ValueError(
                f'text: size {mapping["size"]:g} mm does not fit the opening (leave size out: '
                'the largest that fits)'
            )
        return lay, shift
    lo, hi = 4, max(8, shape[0])  # font px: lo fits, hi does not
    if not attempt(lo, 1.0)[2]:
        raise ValueError('text: does not fit the opening even at the smallest size')
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if attempt(mid, 1.0)[2] else (lo, mid)
    best = attempt(lo, 1.0)
    if not mapping.get('line_height'):
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
    mask = opening(cfg, mapping['objects'][0])
    key = _key(mapping, font_path, mask)
    count = sum(1 for line in lines_of(mapping) if line['text'].strip())
    reveal = bool(mapping.get('reveal'))
    names = (
        [f'{TEXT_DIR}/{key}-{k:02d}.png' for k in range(count + 1)]
        if reveal
        else [f'{TEXT_DIR}/{key}.png']
    )
    if not all((folder / n).exists() for n in names):
        lay, (dx, dy) = layout(mapping, font_path, mask)
        (folder / TEXT_DIR).mkdir(exist_ok=True)
        steps = range(count + 1) if reveal else [None]
        for name, upto in zip(names, steps):
            ink = _shift(lay.ink(upto), mask.shape, dx, dy)
            _write(folder / name, _picture(ink, mapping))
    out = {k: v for k, v in mapping.items() if k not in KEYS}
    out['text'] = mapping['text']  # kept for the export's scenes sheet
    if reveal:
        out['slideshow'] = names
        out['loop'] = False
    else:
        out['image'] = names[0]
    return out
