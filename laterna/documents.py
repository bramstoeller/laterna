"""The PDF documents of the export (laterna/export.py), with reportlab.

Layout only: export.py renders the pictures and hands them in with the
parsed config and scenes. Four documents:

  config_sheet  calibration, frame shapes, look, the pretend spot, DMX
                and config.yaml itself (A4 landscape)
  run_sheet     the operator's cue list (cue sheet, draaiboek): every scene
                numbered as play.py's L label numbers it, drawn plainly,
                how it is reached and what is on each object (A4 landscape)
  scenes_sheet  the scenes' values from scenes.yaml in a table
  backup        every scene full screen, one page per picture at the
                canvas's aspect ratio, in show order (blackouts as black
                pages, one page per distinct combination of slideshow
                pictures), with an outline per scene

The page helpers work top-down in points (y grows downwards), like the
screen; Doc converts to reportlab's bottom-up coordinates. Only the
standard PDF fonts are used (no font files to ship), so the text sticks
to their character set (Latin-1 plus a few typographic signs: no arrows).
"""

import datetime
import io
import math

import cv2
import numpy as np
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from . import i18n
from .i18n import tr

A4_LANDSCAPE = landscape(A4)
M = 36  # page margin

INK = (0.13, 0.12, 0.11)
MUTED = (0.42, 0.40, 0.37)
FAINT = (0.80, 0.78, 0.74)
PAPER = (1.0, 1.0, 1.0)
GOLD = (0.66, 0.50, 0.19)
PANEL = (0.96, 0.95, 0.92)
GREEN = (0.24, 0.48, 0.24)
ORANGE = (0.75, 0.48, 0.10)
RED = (0.75, 0.22, 0.17)
BLUE = (0.18, 0.40, 0.56)
DESK = (0.15, 0.28, 0.75)  # a step the desk can take too (the blue state block)
STRIP_OFF = (0.92, 0.91, 0.88)  # a scene cell in the strip
NIGHT = (0.04, 0.04, 0.04)
WOOD = (0.16, 0.12, 0.08)
GILT = (0.82, 0.67, 0.33)
CLOTH = (0.96, 0.95, 0.89)

FONT, BOLD, MONO = 'Helvetica', 'Helvetica-Bold', 'Courier'


def today():
    return datetime.date.today().isoformat()


def num(value, digits=1):
    """A number without trailing zeros."""
    s = f'{float(value):.{digits}f}'
    return s.rstrip('0').rstrip('.') if '.' in s else s


def mmss(seconds):
    s = int(round(seconds))
    return f'{s // 60}:{s % 60:02d}'


def secs(seconds):
    return f'{num(seconds, 2)} s'


def duration(seconds):
    """A duration in seconds under a minute, else in minutes: 55 s, 3 m, 2.5 m."""
    return secs(seconds) if seconds < 60 else f'{num(seconds / 60, 1)} m'


def jpeg(image, max_width=None, quality=88):
    """RGB uint8 image -> ImageReader holding JPEG bytes (embedded as is)."""
    if max_width and image.shape[1] > max_width:
        h = max(1, round(image.shape[0] * max_width / image.shape[1]))
        image = cv2.resize(image, (max_width, h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(
        '.jpg', cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not ok:
        raise RuntimeError('JPEG encoding failed')
    return ImageReader(io.BytesIO(buf.tobytes()))


def crop(image, box):
    x0, y0, x1, y1 = box
    return image[y0:y1, x0:x1]


def show_name(cfg):
    """The show's name: its folder's, dashes as spaces (shows/my-show: my show)."""
    return cfg['_dir'].name.replace('-', ' ').replace('_', ' ')


class Doc:
    """A PDF with top-down page helpers."""

    def __init__(self, path, title, footer=None, pagesize=A4_LANDSCAPE, show=''):
        self.c = rl_canvas.Canvas(str(path), pagesize=pagesize, pageCompression=1)
        self.c.setTitle(f'{show} · {title}' if show else title)
        self.c.setCreator('laterna projection export')
        self.show = show
        self.w, self.h = pagesize
        self.footer = footer
        self.page = 1

    def new_page(self):
        self._footer()
        self.c.showPage()
        self.page += 1

    def save(self):
        self._footer()
        self.c.save()

    def _footer(self):
        if self.footer:
            self.text(M, self.h - 18, self.footer, 7, color=MUTED)
            self.text(self.w - M, self.h - 18, str(self.page), 7, color=MUTED, align='right')

    def bookmark(self, title, level=0):
        key = f'page{self.page}'
        self.c.bookmarkPage(key)
        self.c.addOutlineEntry(title, key, level)

    # --- text --------------------------------------------------------------
    @staticmethod
    def _font(bold, mono):
        return MONO if mono else (BOLD if bold else FONT)

    def width(self, s, size, bold=False, mono=False):
        return stringWidth(s, self._font(bold, mono), size)

    def text(self, x, y, s, size, bold=False, color=INK, align='left', mono=False):
        """Text with its baseline at y; returns its width."""
        font = self._font(bold, mono)
        w = stringWidth(s, font, size)
        if align == 'right':
            x -= w
        elif align == 'center':
            x -= w / 2
        self.c.setFillColorRGB(*color)
        self.c.setFont(font, size)
        self.c.drawString(x, self.h - y, s)
        return w

    def fit(self, s, size, width, bold=False, mono=False):
        """s shortened with '...' until it fits width."""
        if self.width(s, size, bold, mono) <= width:
            return s
        while s and self.width(s + '...', size, bold, mono) > width:
            s = s[:-1]
        return s + '...'

    def wrap(self, s, size, width, bold=False, mono=False):
        lines = []
        for para in str(s).split('\n'):
            line = ''
            for word in para.split(' '):
                trial = f'{line} {word}'.strip()
                if not line or self.width(trial, size, bold, mono) <= width:
                    line = trial
                else:
                    lines.append(line)
                    line = word
            lines.append(line)
        return lines

    def para(self, x, y, s, size, width, bold=False, color=INK, lead=1.35, mono=False):
        """Wrapped text from baseline y; returns the baseline below it."""
        for line in self.wrap(s, size, width, bold, mono):
            self.text(x, y, line, size, bold, color, mono=mono)
            y += size * lead
        return y

    def label(self, x, y, s, fill, size=6.5, color=PAPER, pad=3):
        """A small pill with its top-left at (x, y); returns its width."""
        w = self.width(s, size, True) + 2 * pad
        self.rect(x, y, w, size + 2 * pad - 1, fill=fill, r=2)
        self.text(x + pad, y + size + pad - 1.5, s, size, True, color)
        return w

    def key(self, x, y, name, size=7.5):
        """A key cap with its baseline at y (LEFT / RIGHT: an arrow, like
        on the keys themselves; the standard fonts have none); returns its
        width."""
        arrow = name in (LEFT, RIGHT)
        w = 16 if arrow else self.width(name, size, True) + 8
        self.rect(x, y - size - 1.5, w, size + 5, fill=PAPER, stroke=MUTED, lw=0.6, r=2)
        if arrow:
            cy, d = y - size / 2 + 1, 1 if name == RIGHT else -1
            tip, tail = x + w / 2 + d * 4.5, x + w / 2 - d * 4.5
            self.line(tail, cy, tip - d * 2, cy, INK, 1.0)
            self.poly([(tip, cy), (tip - d * 3.2, cy - 2.4), (tip - d * 3.2, cy + 2.4)], fill=INK)
        else:
            self.text(x + 4, y, name, size, True)
        return w

    def section(self, x, y, title):
        self.text(x, y, title.upper(), 7.5, True, GOLD)
        return y + 13

    # --- shapes ------------------------------------------------------------
    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.5, r=0):
        c = self.c
        if fill is not None:
            c.setFillColorRGB(*fill)
        if stroke is not None:
            c.setStrokeColorRGB(*stroke)
            c.setLineWidth(lw)
        c.setDash()
        args = (x, self.h - y - h, w, h)
        if r:
            c.roundRect(*args, r, stroke=int(stroke is not None), fill=int(fill is not None))
        else:
            c.rect(*args, stroke=int(stroke is not None), fill=int(fill is not None))

    def line(self, x0, y0, x1, y1, color=FAINT, lw=0.5, dash=None):
        c = self.c
        c.setStrokeColorRGB(*color)
        c.setLineWidth(lw)
        c.setDash(dash or [])
        c.line(x0, self.h - y0, x1, self.h - y1)
        c.setDash()

    def poly(self, pts, fill=None, stroke=None, lw=0.5, close=True, dash=None):
        c = self.c
        path = c.beginPath()
        path.moveTo(pts[0][0], self.h - pts[0][1])
        for x, y in pts[1:]:
            path.lineTo(x, self.h - y)
        if close:
            path.close()
        if fill is not None:
            c.setFillColorRGB(*fill)
        if stroke is not None:
            c.setStrokeColorRGB(*stroke)
            c.setLineWidth(lw)
            c.setDash(dash or [])
        c.drawPath(path, stroke=int(stroke is not None), fill=int(fill is not None))
        c.setDash()

    def dot(self, x, y, r, color):
        self.c.setFillColorRGB(*color)
        self.c.circle(x, self.h - y, r, stroke=0, fill=1)

    def image(self, reader, x, y, w, h):
        self.c.drawImage(reader, x, self.h - y - h, w, h)


def table(doc, x, y, rows, widths, size=8, head=None, lead=13):
    """Rows of strings, the first column bold, zebra striped; returns the y below."""
    if head:
        cx = x
        for t, w in zip(head, widths):
            doc.text(cx, y, t.upper(), 6.5, True, MUTED)
            cx += w
        y += 5
        doc.line(x, y, x + sum(widths), y)
        y += lead - 3
    for k, row in enumerate(rows):
        if k % 2 == 0:
            doc.rect(x - 3, y - size - 1.5, sum(widths) + 6, lead, fill=PANEL)
        cx = x
        for j, (t, w) in enumerate(zip(row, widths)):
            doc.text(cx, y, doc.fit(str(t), size, w - 4, j == 0), size, j == 0)
            cx += w
        y += lead
    return y


# ============================================================================
# the scenes as the operator meets them


def scene_facts(scenes, fades):
    """Per scene: how it is reached and what it does, as play.py plays it.

    entry: 'start' (shown when the show starts), 'key' (a step key) or
    'auto' (the previous scene's hold ran out); fade: the transition into
    it; hold: seconds it stands before moving on by itself (None = waits)."""
    facts = []
    for i, st in enumerate(scenes):
        prev = scenes[i - 1] if i else None
        entry = 'start' if i == 0 else ('auto' if prev.get('hold') is not None else 'key')
        facts.append(
            {
                'entry': entry,
                'fade': fades[i - 1] if i else 0.0,
                'hold': st.get('hold'),
                'last': i == len(scenes) - 1,
            }
        )
    return facts


def _media_label(m):
    name = (m.get('image') or m.get('video') or '').replace('\\', '/').split('/')[-1]
    stem = name.rsplit('.', 1)[0]
    if 'video' in m:
        return 'video', tr('run.video_of', name=stem)
    if 'slideshow' in m:
        return 'slideshow', tr('run.slideshow_of', n=len(m['slideshow']))
    return 'image', stem


def object_contents(scene, cfg):
    """{object id: (kind, text)} of what a scene shows on each object;
    kind is black, fill, image, video or slideshow."""
    if scene.get('blackout'):
        return {o['id']: ('black', tr('run.black')) for o in cfg['objects']}
    out = {o['id']: ('fill', tr('run.fill')) for o in cfg['objects']}
    for m in scene.get('mappings', []):
        for oid in m['objects']:
            out[oid] = _media_label(m)
    return out


ENTRY = {'start': ('START', MUTED), 'key': ('KEY', GREEN), 'auto': ('AUTO', ORANGE)}


def _step_mark(doc, mid, y, kind, gap):
    """The step into a scene, drawn in the gap before its cell (top at y):
    key / desk a line, auto a wedge pointing on."""
    if kind == 'auto':
        w = gap / 2 + 1.5
        doc.poly([(mid - w, y + 2), (mid + w, y + 7), (mid - w, y + 12)], fill=ORANGE)
    else:
        doc.rect(mid - 0.9, y - 1.5, 1.8, 17, fill=DESK if kind == 'desk' else GREEN)


def _media_icon(doc, x, y, kind, color):
    """A small icon, 7 x 5 pt with its top-left at (x, y): video a play
    triangle, slideshow two stacked frames."""
    if kind == 'video':
        doc.poly([(x + 1.5, y), (x + 6.5, y + 2.5), (x + 1.5, y + 5)], fill=color)
    else:
        doc.rect(x + 2, y, 5, 3.6, stroke=color, lw=0.6)
        doc.rect(x, y + 1.4, 5, 3.6, fill=STRIP_OFF, stroke=color, lw=0.6)


def _scene_kinds(scene):
    """The media kinds a scene shows, for the strip's icons."""
    maps = scene.get('mappings', [])
    return [k for k in ('slideshow', 'video') if any(k in m for m in maps)]


def desk_steps(cfg, scenes, facts):
    """Per scene: whether the desk can take the show into it too (a key
    step next to a blackout, see play.py), with a desk configured."""
    if ((cfg.get('dmx') or {}).get('source') or 'off') == 'off':
        return [False] * len(scenes)
    return [
        bool(j)
        and facts[j]['entry'] == 'key'
        and bool(scenes[j].get('blackout') or scenes[j - 1].get('blackout'))
        for j in range(len(scenes))
    ]


def _strip(doc, y, scenes, facts, desk, on_page=None):
    """All scenes as numbered cells across the page, a blackout framed in
    black, with an icon for a slideshow or a video; the step into each is
    drawn in the gap before it: a green line = on a key, blue = on a key or
    by the desk (a blackout on either side, see play.py), an orange wedge =
    by itself. on_page = (first, last): a dimension line under this page's scenes."""
    n = len(scenes)
    gap = 5 if n <= 40 else (3 if n <= 80 else 2)
    cell = (doc.w - 2 * M - gap * (n - 1)) / n
    for j in range(n):
        x = M + j * (cell + gap)
        dark = bool(scenes[j].get('blackout'))
        if dark:  # a blackout: framed in black, no inked block
            doc.rect(x + 0.6, y + 0.6, cell - 1.2, 12.8, fill=STRIP_OFF, stroke=NIGHT, lw=1.2)
        else:
            doc.rect(x, y, cell, 14, fill=STRIP_OFF)
        if j:  # the step into scene j
            kind = 'auto' if facts[j]['entry'] == 'auto' else ('desk' if desk[j] else 'key')
            _step_mark(doc, x - gap / 2, y, kind, gap)
        if cell >= 11:
            doc.text(
                x + cell / 2,
                y + 10,
                str(j + 1),
                6.5 if cell >= 15 else 5,
                False,
                INK,
                'center',
            )
        if cell >= 30:
            for k, kind in enumerate(_scene_kinds(scenes[j])):
                _media_icon(doc, x + cell - 10 - 9 * k, y + 4.5, kind, MUTED)
    if on_page:
        first, last = on_page
        x0, x1 = M + first * (cell + gap), M + last * (cell + gap) + cell
        # like a dimension in a technical drawing: a tick at either end, a line between
        doc.line(x0, y + 18, x1, y + 18, MUTED, 0.8)
        for x in (x0, x1):
            doc.line(x, y + 15, x, y + 21, MUTED, 0.8)
    return y + 14


def _strip_legend(doc, x, y, desk=True):
    """What the strip's marks mean, drawn as they are, on one line from x
    (baseline y); `desk` False leaves the desk's steps out (no desk)."""
    for mark, key in (
        ('key', 'legend.step_key'),
        ('desk', 'legend.step_desk'),
        ('auto', 'legend.step_auto'),
        ('blackout', 'legend.blackout'),
        ('slideshow', 'legend.slideshow'),
        ('video', 'legend.video'),
    ):
        if mark == 'desk' and not desk:
            continue
        if mark in ('key', 'desk', 'auto'):
            _step_mark(doc, x + 3, y - 10.5, mark, 5)
            x += 9
        elif mark == 'blackout':
            doc.rect(x + 0.5, y - 6.5, 9, 6, fill=STRIP_OFF, stroke=NIGHT, lw=1.2)
            x += 13
        else:
            _media_icon(doc, x, y - 5.5, mark, MUTED)
            x += 10
        x += doc.text(x, y, tr(key), 8, color=MUTED) + 16


def _page_head(doc, title):
    """The show and the document, small, top left: what the page is of."""
    head = doc.show.upper()
    doc.text(M, 30, head, 8, True, GOLD)
    doc.text(M + doc.width(head, 8, True) + 10, 30, title, 8, color=MUTED)


LEFT, RIGHT = '<left>', '<right>'  # arrow keys, drawn (Doc.key)
# the keys of the presentation: the caps (an i18n key for a translated
# name) and i18n key.<k>_does
KEYS = [
    ('next', ['Enter', 'key.space', RIGHT]),
    ('back', ['Backspace', LEFT]),
    ('twice', [RIGHT, RIGHT]),
    ('slide', ['<', '>']),
    ('l', ['L']),
    ('p', ['P']),
    ('q', ['Q', 'Esc']),
]
BLOCKS = [  # colour, i18n block.<name> and block.<name>_means, blocks drawn
    (GREEN, 'green', 1),
    (DESK, 'blue', 1),
    (ORANGE, 'orange', 4),
    (RED, 'red', 4),
    ((0.35, 0.35, 0.35), 'grey', 4),
]


def run_sheet(path, cfg, scenes, fades, views, crop_box, source, description=None):
    """The operator's cue list. views[i] = export.scene_views of scene i
    (thumb None for a blackout); description = the scenes file's own, and
    each scene's `description` goes under its row."""
    i18n.use(cfg.get('language'))
    facts = scene_facts(scenes, fades)
    desk = desk_steps(cfg, scenes, facts)
    n = len(scenes)
    show = show_name(cfg)
    doc = Doc(
        path,
        tr('run.title'),
        tr('run.footer', source=source, date=today()),
        show=show,
    )
    thumbs = {}

    def thumb(i, k):
        if (i, k) not in thumbs:
            img = views[i][k]['thumb']
            thumbs[(i, k)] = None if img is None else jpeg(img, quality=85)
        return thumbs[(i, k)]

    aspect = (crop_box[3] - crop_box[1]) / (crop_box[2] - crop_box[0])

    def picture(i, k, x, y, w):
        h = w * aspect
        reader = thumb(i, k)
        if reader is None:  # a blackout: framed in black as in the strip, no inked block
            doc.rect(x + 0.6, y + 0.6, w - 1.2, h - 1.2, fill=STRIP_OFF, stroke=NIGHT, lw=1.2)
            doc.text(
                x + w / 2, y + h / 2 + 2, 'BLACKOUT', 5.5 if w < 80 else 7, True, MUTED, 'center'
            )
        else:
            doc.image(reader, x, y, w, h)
        return h

    # --- cover ------------------------------------------------------------
    _page_head(doc, tr('run.title'))
    y = 44
    if description:
        y = doc.para(M, y + 4, description, 10, doc.w - 2 * M) + 8
        doc.line(M, y - 4, doc.w - M, y - 4)
        y += 8
    y = _strip(doc, y, scenes, facts, desk)
    _strip_legend(doc, M, y + 13, any(desk))

    x2 = M + 380
    doc.line(M, y + 30, doc.w - M, y + 30)
    top = y + 50
    y = doc.section(M, top, tr('run.keys'))
    for k, caps in KEYS:
        x = M
        for cap in caps:
            x += doc.key(x, y, tr(cap) if cap.startswith('key.') else cap, 8.5) + 4
        below = doc.para(M + 170, y, tr(f'key.{k}_does'), 9, x2 - M - 180)
        y = max(y + 19, below + 6)
    y = doc.section(M, y + 18, tr('run.blocks'))
    for col, name, count in BLOCKS:
        for b in range(count):
            corner = b == (0 if name == 'grey' else count - 1)
            bx = M + b * 6 + (18 if count == 1 else 0)
            doc.rect(
                bx,
                y - 7,
                4,
                4,
                fill=col if corner or name == 'grey' else tuple(c * 0.5 for c in col),
            )
        doc.text(M + 30, y - 3, tr(f'block.{name}'), 9, True)
        y = doc.para(M + 76, y - 3, tr(f'block.{name}_means'), 9, x2 - M - 96) + 5

    y = doc.section(x2, top, tr('run.trouble'))
    y = doc.para(x2, y, tr('run.trouble_restart'), 9.5, doc.w - M - x2)
    y = doc.para(x2, y + 6, tr('run.trouble_backup'), 9.5, doc.w - M - x2)
    y = doc.section(x2, y + 18, tr('run.columns'))
    doc.para(
        x2,
        y,
        tr('run.columns_note'),
        9,
        doc.w - M - x2,
        color=MUTED,
    )

    # --- cue list ---------------------------------------------------------
    objects = cfg['objects']
    split = len(objects) <= 4
    X_NUM, X_PIC, PIC_W = M, M + 24, 104
    X_NAME = X_PIC + PIC_W + 10
    X_IN = X_NAME + 150
    X_OBJ = X_IN + 80
    X_END = doc.w - M
    obj_w = (X_END - X_OBJ - 6) / (len(objects) if split else 1)
    names = {o['id']: str(o.get('name', o['id'])) for o in objects}

    def head(first, last):
        _page_head(doc, tr('run.title'))
        _strip(doc, 40, scenes, facts, desk, on_page=(first, last))
        y = 72
        cols = [
            (X_NUM, '#'),
            (X_PIC, tr('col.picture')),
            (X_NAME, tr('run.scene')),
            (X_IN, tr('col.in')),
        ]
        if split:
            cols += [
                (X_OBJ + k * obj_w, f'{o["id"]} {names[o["id"]]}') for k, o in enumerate(objects)
            ]
        else:
            cols += [(X_OBJ, tr('col.objects'))]
        for x, t in cols:
            doc.text(x, y, doc.fit(t.upper(), 7.5, 110, True), 7.5, True, MUTED)
        return y + 6

    def notes(i):
        """The scene's description, wrapped to the scene column."""
        text = scenes[i].get('description')
        return doc.wrap(' '.join(str(text).split()), 8, X_IN - X_NAME - 10) if text else []

    def notes_top(i):
        """Where the description starts, under the name."""
        return 27

    def row_height(i):
        h = max(PIC_W * aspect + 10, notes_top(i) + len(notes(i)) * 9.5)
        if len(views[i]) > 1:
            per_line = int((X_END - X_NAME) // 76)
            lines = math.ceil(len(views[i]) / per_line)
            h += lines * (62 * aspect + 18) + 4
        if not split:
            h = max(h, 14 + 10 * len(objects))
        return h

    bottom = doc.h - 34
    pages, current, y = [], [], 78
    for i in range(n):
        h = row_height(i)
        if current and y + h > bottom:
            pages.append(current)
            current, y = [], 78
        current.append(i)
        y += h
    pages.append(current)

    for rows in pages:
        doc.new_page()
        y = head(rows[0], rows[-1])
        for i in rows:
            st, f = scenes[i], facts[i]
            h = row_height(i)
            doc.line(M, y, doc.w - M, y)
            _, col = ENTRY[f['entry']]
            doc.rect(X_NUM, y + 4, 3, h - 8, fill=col)
            doc.text(X_NUM + 7, y + 17, str(i + 1), 12, True)
            picture(i, 0, X_PIC, y + 5, PIC_W)
            doc.text(
                X_NAME, y + 15, doc.fit(st.get('name', '?'), 10, X_IN - X_NAME - 8, True), 10, True
            )
            # in
            _, lc = ENTRY[f['entry']]
            label = tr(f'entry.{f["entry"]}')
            if f['entry'] == 'auto':  # the time it comes after: the hold before + the fade
                label += f' {duration(scenes[i - 1]["hold"] + f["fade"])}'
            w = doc.label(X_IN, y + 7, label, lc)
            if desk[i]:  # the desk can take this step too
                doc.label(X_IN + w + 3, y + 7, 'DMX', DESK)
            if i:
                doc.text(X_IN, y + 29, tr('run.fade', t=secs(f['fade'])), 8, color=MUTED)
            # objects
            now = object_contents(st, cfg)
            before = object_contents(scenes[i - 1], cfg) if i else {}
            if st.get('blackout'):
                doc.text(X_OBJ, y + 15, tr('run.all_black'), 9, color=MUTED)
            elif split:
                for k, o in enumerate(objects):
                    kind, t = now[o['id']]
                    slides = kind == 'slideshow'
                    same = before.get(o['id']) == (kind, t) and not slides
                    x = X_OBJ + k * obj_w
                    lines = doc.wrap(t.replace('-', ' '), 8.5, obj_w - 6, slides)
                    for li, line in enumerate(lines[:3]):
                        doc.text(
                            x,
                            y + 15 + li * 10.5,
                            line,
                            8.5,
                            slides,
                            MUTED if same or kind == 'fill' else INK,
                        )
                    if same:
                        doc.text(
                            x, y + 15 + min(len(lines), 3) * 10.5, tr('run.stays'), 7.5, color=MUTED
                        )
            else:
                for k, o in enumerate(objects):
                    kind, t = now[o['id']]
                    doc.text(
                        X_OBJ,
                        y + 15 + k * 10,
                        doc.fit(f'{o["id"]}: {t}', 8.5, X_END - X_OBJ - 6),
                        7.5,
                        color=MUTED if before.get(o['id']) == (kind, t) else INK,
                    )
            # the scene's description under its name, then the slideshow combinations
            for k, line in enumerate(notes(i)):
                doc.text(X_NAME, y + notes_top(i) + k * 9.5, line, 8, color=MUTED)
            sy = y + max(PIC_W * aspect + 12, notes_top(i) + len(notes(i)) * 9.5 + 2)
            if len(views[i]) > 1:
                per_line = int((X_END - X_NAME) // 76)
                for k, v in enumerate(views[i]):
                    x = X_NAME + (k % per_line) * 76
                    ty = sy + (k // per_line) * (62 * aspect + 18)
                    th = picture(i, k, x, ty, 68)
                    changed = ', '.join(v['changed']) if v.get('changed') else ''
                    doc.text(x, ty + th + 9, doc.fit(f'{k + 1}. {changed}', 7, 72, True), 7, True)
            y += h
        doc.line(M, y, doc.w - M, y)
    doc.save()


def backup(path, cfg, scenes, views):
    """Every picture full screen, at the canvas's aspect ratio. Returns the
    first page number of every scene."""
    i18n.use(cfg.get('language'))
    w, h = cfg['canvas']
    size = (w * 0.5, h * 0.5)  # 960 x 600 pt at 1920 x 1200: the pixels at 144 dpi
    doc = Doc(path, tr('backup.title'), pagesize=size, show=show_name(cfg))
    firsts = []
    n = len(scenes)
    for i, st in enumerate(scenes):
        firsts.append(doc.page if i == 0 else doc.page + 1)
        for k, v in enumerate(views[i]):
            if i or k:
                doc.new_page()
            if v['full'] is None:
                doc.rect(0, 0, size[0], size[1], fill=(0, 0, 0))
            else:
                doc.image(ImageReader(io.BytesIO(v['full'])), 0, 0, size[0], size[1])
            title = f'{i + 1}/{n} {st.get("name", "?")}'
            if len(views[i]) > 1:
                title += f' [{k + 1}/{len(views[i])}]'
            doc.bookmark(title)
    doc.c.showOutline()
    doc.save()
    return firsts


# ============================================================================
# the config


class View:
    """World mm (y up) -> page points (y down) inside a box, uniform scale."""

    def __init__(self, box, lo, hi, pad=0.0):
        x, y, w, h = box
        (x0, y0), (x1, y1) = (lo[0] - pad, lo[1] - pad), (hi[0] + pad, hi[1] + pad)
        self.s = min(w / (x1 - x0), h / (y1 - y0))
        self.ox = x + (w - (x1 - x0) * self.s) / 2 - x0 * self.s
        self.oy = y + (h + (y1 - y0) * self.s) / 2 + y0 * self.s

    def __call__(self, p):
        return self.ox + p[0] * self.s, self.oy - p[1] * self.s

    def pts(self, ps):
        return [self(p) for p in ps]


def _draw_frame(doc, v, g, detail=True):
    doc.poly(v.pts(g['wood']), fill=WOOD)
    for p in g['polygons']:
        doc.poly(v.pts(p), fill=GILT)
    for p in g['canvas']:
        doc.poly(v.pts(p), fill=CLOTH)
    if detail:
        doc.poly(v.pts(g['wood']), stroke=INK, lw=0.4, dash=[2, 1.5])
        for p in g['canvas']:
            doc.poly(v.pts(p), stroke=INK, lw=0.7)


def _dim_h(doc, v, xa, xb, y, text, off=0.0, color=MUTED):
    (x0, yy), (x1, _) = v((xa, y)), v((xb, y))
    yy += off
    doc.line(x0, yy, x1, yy, color)
    for x in (x0, x1):
        doc.line(x, yy - 3, x, yy + 3, color)
    doc.text((x0 + x1) / 2, yy - 2.5, text, 6.5, color=color, align='center')


def _dim_v(doc, v, ya, yb, x, text, off=0.0, color=MUTED):
    (xx, y0), (_, y1) = v((x, ya)), v((x, yb))
    xx += off
    doc.line(xx, y0, xx, y1, color)
    for y in (y0, y1):
        doc.line(xx - 3, y, xx + 3, y, color)
    c = doc.c
    c.saveState()
    c.translate(xx - 3, doc.h - (y0 + y1) / 2)
    c.rotate(90)
    c.setFillColorRGB(*color)
    c.setFont(FONT, 6.5)
    c.drawCentredString(0, 0, text)
    c.restoreState()


def _value(v):
    if isinstance(v, float):
        return num(v, 3)
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(_value(x) for x in v) + ']'
    if isinstance(v, dict):
        return '{' + ', '.join(f'{k}: {_value(x)}' for k, x in v.items()) + '}'
    return str(v)


# the look and spot keys with a note (i18n look.<key>, spot.<key>)
LOOK_NOTES = ('molding', 'fill', 'images', 'temperature', 'white', 'spot_collapse', 'frame_depth')
SPOT_NOTES = (
    'type',
    'strength',
    'position',
    'aim',
    'distance',
    'angle',
    'softness',
    'falloff',
    'size',
    'color',
    'images',
    'fill',
)


def config_sheet(path, cfg, config_text, geometry, extras, dmx_info, source):
    """The calibration, the shapes and the look. geometry: per object the
    world-mm outlines (export.object_geometry); extras: the renders
    (export.config_renders); dmx_info: (settings, [(channel, labels)]) or None."""
    i18n.use(cfg.get('language'))
    show = show_name(cfg)
    doc = Doc(
        path,
        tr('config.title'),
        tr('config.footer', source=source, date=today()),
        show=show,
    )
    mmpp = float(cfg['scale_mm_per_px'])
    cw, ch = cfg['canvas']
    ox, oy = (float(v) for v in cfg.get('image_offset', [0, 0]))
    look = extras['look']
    crop_box = extras['crop']

    def head(title):
        _page_head(doc, tr('config.title'))
        doc.line(M, 38, doc.w - M, 38)
        doc.text(M, 64, title, 18, True)

    # --- overview ---------------------------------------------------------
    head(tr('config.setup'))
    if cfg.get('description'):
        doc.text(
            M + doc.width(tr('config.setup'), 18, True) + 14,
            64,
            doc.fit(' '.join(str(cfg['description']).split()), 9, 300),
            9,
            color=MUTED,
        )
    hw, fh = cw * mmpp / 2, ch * mmpp
    screen = [(-hw + ox, oy), (hw + ox, oy), (hw + ox, oy + fh), (-hw + ox, oy + fh)]
    v = View((M + 12, 80, 500, 320), (-hw + ox, oy - 300), (hw + ox, oy + fh))
    doc.poly(v.pts(screen), fill=NIGHT)
    for g in geometry:
        _draw_frame(doc, v, g, detail=False)
        doc.dot(*v(g['origin']), 2, RED)
        top = max(p[1] for p in g['wood'])
        doc.text(
            v((g['origin'][0], top))[0], v((0, top))[1] - 5, str(g['id']), 8, True, PAPER, 'center'
        )
    proj = cfg.get('projector')
    if proj:
        p = v(proj['position'])
        doc.dot(*p, 3, BLUE)
        doc.text(p[0] + 6, p[1] + 3, tr('config.lens_foot'), 6.5, color=(0.6, 0.75, 0.9))
    _dim_h(
        doc,
        v,
        -hw + ox,
        hw + ox,
        oy,
        tr('config.picture_width', mm=num(cw * mmpp, 0), px=cw),
        off=26,
    )
    _dim_v(doc, v, oy, oy + fh, -hw + ox, f'{num(fh, 0)} mm = {ch} px', off=-6)
    spans = sorted((min(p[0] for p in g['wood']), max(p[0] for p in g['wood'])) for g in geometry)
    if spans:
        _dim_h(
            doc,
            v,
            spans[0][0],
            spans[-1][1],
            oy,
            tr('config.outer_edges', mm=num(spans[-1][1] - spans[0][0], 0)),
            off=12,
        )
        low = min(min(p[1] for p in g['wood']) for g in geometry)
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            if b0 > a1:
                _dim_h(doc, v, a1, b0, low + 600, num(b0 - a1, 1), color=(0.75, 0.72, 0.66))
    doc.para(
        M,
        432,
        tr('config.setup_note'),
        7,
        520,
        color=MUTED,
    )

    rx = M + 545
    y = doc.section(rx, 90, tr('config.global'))
    y = table(
        doc,
        rx,
        y,
        [
            ('canvas', f'{cw} x {ch} px'),
            ('scale_mm_per_px', f'{num(mmpp, 3)} mm/px'),
            (tr('config.picture_on_plane'), f'{num(cw * mmpp, 0)} x {num(fh, 0)} mm'),
            ('rotation', tr('unit.deg', v=num(cfg.get('rotation', 0), 3))),
            ('image_offset', f'[{num(ox)}, {num(oy)}] mm'),
            ('gamma', _value(cfg.get('gamma', 'none'))),
        ],
        [100, 125],
    )
    if proj:
        y = doc.section(rx, y + 10, tr('config.projector'))
        y = table(
            doc,
            rx,
            y,
            [
                ('position', _value(proj['position']) + ' mm'),
                ('distance', f'{num(proj["distance"])} mm'),
                ('frame_depth', tr('unit.mm_off', v=num(look.get('frame_depth', 0)))),
            ],
            [100, 125],
        )
    y = doc.section(rx, y + 10, tr('config.objects'))
    rows = []
    for g in geometry:
        wood = np.asarray(g['wood'])
        (bx0, by0), (bx1, by1) = g['bbox_px']
        rows.append(
            (
                str(g['id']),
                str(g['name']),
                f'{num(np.ptp(wood[:, 0]), 0)} x {num(np.ptp(wood[:, 1]), 0)}',
                f'{num(bx0, 0)}-{num(bx1, 0)}, {num(by0, 0)}-{num(by1, 0)}',
            )
        )
    y = table(
        doc,
        rx,
        y,
        rows,
        [22, 70, 62, 80],
        head=['id', tr('col.name'), tr('col.wood_mm'), 'pixels x, y'],
        size=7.5,
        lead=12,
    )
    doc.para(
        rx,
        y + 6,
        tr('config.wood_note'),
        7,
        doc.w - M - rx,
        color=MUTED,
    )

    # --- one page per object ----------------------------------------------
    for g in geometry:
        doc.new_page()
        head(tr('config.object', id=g['id'], name=g['name']))
        if g.get('description'):
            doc.para(M, 80, ' '.join(str(g['description']).split()), 8, 420, color=MUTED)
        inner = np.asarray(g['inner_local'], float)
        org = np.asarray(g['origin'], float)

        def local(ps, org=org):
            return [tuple(np.asarray(p) - org) for p in ps]

        lg = {
            'wood': local(g['wood']),
            'polygons': [local(p) for p in g['polygons']],
            'canvas': [local(p) for p in g['canvas']],
        }
        wood = np.asarray(lg['wood'])
        lo, hi = wood.min(0), wood.max(0)
        v = View((M + 34, 80, 390, 440), lo, hi, pad=0.06 * max(hi - lo))
        _draw_frame(doc, v, lg)
        label_corners = len(inner) <= 40
        cx = inner[:, 0].mean()
        for k, p in enumerate(inner):
            q = v(p)
            doc.dot(*q, 2.0, RED)
            if label_corners:
                dx = -1 if p[0] < cx - 1 else (1 if p[0] > cx + 1 else 0)
                doc.text(
                    q[0] + dx * 8,
                    q[1] - 5,
                    str(k + 1),
                    6.5,
                    True,
                    RED,
                    'right' if dx < 0 else ('left' if dx > 0 else 'center'),
                )
        o = v((0, 0))
        doc.line(o[0] - 6, o[1], o[0] + 6, o[1], BLUE, 0.8)
        doc.line(o[0], o[1] - 6, o[0], o[1] + 6, BLUE, 0.8)
        doc.text(o[0] + 5, o[1] - 10, tr('config.origin'), 6.5, color=BLUE)
        _dim_h(doc, v, lo[0], hi[0], lo[1], tr('config.wood_size', v=num(hi[0] - lo[0], 1)), off=24)
        _dim_h(
            doc,
            v,
            inner[:, 0].min(),
            inner[:, 0].max(),
            lo[1],
            tr('config.canvas_size', v=num(np.ptp(inner[:, 0]), 1)),
            off=12,
        )
        _dim_v(
            doc, v, lo[1], hi[1], lo[0], tr('config.wood_size', v=num(hi[1] - lo[1], 1)), off=-24
        )
        _dim_v(
            doc,
            v,
            inner[:, 1].min(),
            inner[:, 1].max(),
            hi[0],
            tr('config.canvas_size', v=num(np.ptp(inner[:, 1]), 1)),
            off=22,
        )
        lx = M
        for fill, t in (
            (WOOD, 'wood'),
            (GILT, 'molding'),
            (CLOTH, 'canvas'),
            (RED, 'corner'),
        ):
            doc.rect(lx, 541, 8, 8, fill=fill, stroke=INK, lw=0.3)
            lx += doc.text(lx + 12, 548, tr(f'legend.{t}'), 7, color=MUTED) + 26

        rx = M + 470
        y = doc.section(rx, 90, tr('config.placement'))
        (bx0, by0), (bx1, by1) = g['bbox_px']
        y = table(
            doc,
            rx,
            y,
            [
                ('origin', _value([float(c) for c in g['origin']]) + ' mm'),
                ('scale', num(g['scale'], 4)),
                ('rotation', tr('unit.deg', v=num(g['rotation'], 3))),
                ('border', f'{num(g["border"])} mm'),
                ('pixels', f'x {num(bx0, 1)}-{num(bx1, 1)}, y {num(by0, 1)}-{num(by1, 1)}'),
            ],
            [85, 200],
        )
        y = doc.section(rx, y + 10, tr('config.corners'))
        lead = min(12.2, (doc.h - 110 - y) / (len(inner) + 1))
        size = min(7.8, lead * 0.66)
        rows = [
            (str(k + 1), num(p[0]), num(p[1]), f'{num(q[0], 1)}, {num(q[1], 1)}')
            for k, (p, q) in enumerate(zip(inner, g['corners_px']))
        ]
        y = table(
            doc,
            rx,
            y,
            rows,
            [24, 60, 60, 110],
            head=['#', 'x', 'y', tr('col.projector_px')],
            size=size,
            lead=lead,
        )
        seg = np.linalg.norm(np.diff(np.vstack([inner, inner[:1]]), axis=0), axis=1)
        doc.para(
            rx,
            y + 8,
            tr('config.corners_note', lo=num(seg.min(), 1), hi=num(seg.max(), 1), n=len(inner)),
            7.5,
            doc.w - M - rx,
            color=MUTED,
        )

    # --- molding, light and look -------------------------------------------
    doc.new_page()
    head(tr('config.molding'))
    detail = extras['molding_detail']
    dw = 400
    dh = dw * detail.shape[0] / detail.shape[1]
    doc.image(jpeg(detail, 1000), M, 80, dw, dh)
    doc.para(
        M,
        80 + dh + 12,
        tr('config.molding_note'),
        7,
        dw,
        color=MUTED,
    )
    rx = M + 430
    y = 90
    for title, node, notes in (
        ('border', cfg.get('border') or {}, ()),
        ('light', cfg.get('light') or {}, ()),
        (
            'look',
            {k: x for k, x in look.items() if not isinstance(x, dict) and x is not None},
            LOOK_NOTES,
        ),
    ):
        y = doc.section(rx, y, title)
        y = (
            table(
                doc,
                rx,
                y,
                [(k, _value(x), tr(f'look.{k}') if k in notes else '') for k, x in node.items()],
                [80, 80, 180],
                size=7.8,
                lead=12,
            )
            + 10
        )

    # --- the spot ---------------------------------------------------------
    doc.new_page()
    head(tr('config.spot'))
    iw = (doc.w - 2 * M - 20) / 3
    asp = (crop_box[3] - crop_box[1]) / (crop_box[2] - crop_box[0])
    for k, (img, t) in enumerate(
        (
            (extras['white_spot'], tr('config.with_spot')),
            (extras['gain_map'], tr('config.spot_map')),
            (extras['white_flat'], tr('config.without_spot')),
        )
    ):
        x = M + k * (iw + 10)
        doc.image(jpeg(crop(img, crop_box), 900), x, 78, iw, iw * asp)
        doc.text(x, 78 + iw * asp + 10, t, 7, color=MUTED)
    doc.text(
        M,
        78 + iw * asp + 21,
        tr('config.spot_note'),
        7,
        color=MUTED,
    )

    top = 78 + iw * asp + 36
    rx = M
    y = top + 7
    for title, spot in (
        (tr('config.spot_table'), look.get('spot')),
        ('molding_spot', look.get('molding_spot')),
    ):
        if not spot:
            continue
        y = doc.section(rx, y, title)
        keys = (
            [
                'type',
                'strength',
                'position',
                'aim',
                'distance',
                'angle',
                'softness',
                'falloff',
                'size',
                'color',
                'images',
                'fill',
            ]
            if spot.get('type') == 'cone'
            else ['type', 'strength', 'position', 'size', 'color', 'images', 'fill']
        )
        rows = [
            (k, _value(spot[k]), tr(f'spot.{k}') if k in SPOT_NOTES else '')
            for k in keys
            if k in spot
        ]
        table(doc, rx, y, rows, [60, 80, 220], size=7.5, lead=11.5)
        rx += 390
        y = top + 7

    # --- the spot on a scene -----------------------------------------------
    if extras.get('scene_spot') is not None:
        doc.new_page()
        head(tr('config.scene_spot', name=extras['scene_name']))
        iw = (doc.w - 2 * M - 10) / 2
        for k, (img, t) in enumerate(
            (
                (extras['scene_spot'], tr('config.with_spot_show')),
                (extras['scene_flat'], tr('config.without_spot')),
            )
        ):
            x = M + k * (iw + 10)
            doc.image(jpeg(crop(img, crop_box), 1200), x, 80, iw, iw * asp)
            doc.text(x, 80 + iw * asp + 12, t, 8, True)
        spot = look.get('spot') or {}
        doc.para(
            M,
            80 + iw * asp + 32,
            tr(
                'config.scene_spot_note',
                edge=num(1 - float(spot.get('strength', 0)), 2),
                collapse=num(look.get('spot_collapse', 0), 2),
                kelvin=num(look.get('temperature', 6500), 0),
            ),
            8.5,
            doc.w - 2 * M,
        )

    # --- DMX ----------------------------------------------------------------
    if dmx_info:
        settings, channels = dmx_info
        doc.new_page()
        head('DMX')
        y = doc.section(M, 90, tr('config.dmx_input'))
        rows = [
            (k, _value(settings[k]))
            for k in ('source', 'universe', 'port', 'address', 'cct', 'smooth', 'start')
            if k in settings
        ]
        y = table(doc, M, y, rows, [80, 300])
        y = doc.section(M, y + 12, tr('config.dmx_channels'))
        address = int(settings.get('address', 1))
        rows = [
            (f'{address + off - 1}', f'offset {off}', ' + '.join(labels))
            for off, labels in channels
        ]
        y = table(
            doc,
            M,
            y,
            rows,
            [60, 60, 300],
            head=[tr('col.dmx_channel'), 'offset', tr('col.function')],
        )
        doc.para(
            M,
            y + 10,
            tr('config.dmx_note'),
            8,
            420,
            color=MUTED,
        )

    # --- config.yaml itself -------------------------------------------------
    lines = config_text.rstrip('\n').split('\n')
    per_col = 64
    for p0 in range(0, len(lines), per_col * 2):
        doc.new_page()
        head(tr('config.appendix') + (tr('config.continued') if p0 else ''))
        for c in range(2):
            for k, line in enumerate(lines[p0 + c * per_col : p0 + (c + 1) * per_col]):
                doc.text(
                    M + c * 390, 84 + k * 7.5, doc.fit(line, 6.2, 380, mono=True), 6.2, mono=True
                )
    doc.save()


# ============================================================================
# the scenes, technically


def _mapping_lines(m):
    """What a mapping sets, as short lines: the medium (file names as in
    scenes.yaml, without their folder), then its sizing keys."""

    def base(path):
        return str(path).replace('\\', '/').split('/')[-1]

    if 'slideshow' in m:
        lines = [
            f'slideshow (hold {num(m["hold"], 2)}, {m["transition"]} '
            f'{num(m["transition_time"], 2)})'
        ]
        lines += [f'  {base(p)}' for p in m['slideshow']]
    elif 'video' in m:
        lines = [f'video {base(m["video"])}']
    else:
        lines = [base(m.get('image', '?'))]
    for key in ('fit', 'width', 'height'):
        if key in m:
            lines.append(f'{key} {_value(m[key])}')
    return lines


SAME = ['']  # scenes.pdf: the same picture as the scene before (a line)


def _indent(line):
    """Points a line of _mapping_lines is indented by (a slideshow's pictures)."""
    return 8 if line.startswith(' ') else 0


def scenes_sheet(path, cfg, scenes, fades, data, source):
    """The scenes as scenes.yaml sets them, in a table: per scene its
    timing (the effective hold and the fade to the next), colours and what
    each object shows (blackout for a blackout); the file's defaults on top. A column that is
    empty for every scene is left out. No descriptions: the values only."""
    i18n.use(cfg.get('language'))
    show = show_name(cfg)
    doc = Doc(path, tr('scenes.title'), tr('run.footer', source=source, date=today()), show=show)
    objects = cfg['objects']
    size, lead = 7, 8.6

    def media(s, ids):
        """The lines of what scene s shows on the objects `ids` (one column)."""
        if s.get('blackout'):
            return ['blackout']
        lines = []
        for m in s.get('mappings', []):
            if not set(m['objects']) & set(ids):
                continue
            first, *rest = _mapping_lines(m)
            if len(ids) > 1 or len(m['objects']) > 1:
                first += f'  [{"+".join(str(j) for j in m["objects"])}]'
            lines += [first] + rest
        return lines

    def color(s, key):
        return [_value(list(s[key]))] if key in s else []

    # (name, the media columns that may wrap, bold, lines per scene)
    columns = [
        ('#', False, True, [[str(i + 1)] for i in range(len(scenes))]),
        ('name', False, True, [[str(s.get('name', ''))] for s in scenes]),
        (
            'hold',
            False,
            False,
            [[] if s.get('hold') is None else [num(s['hold'], 2)] for s in scenes],
        ),
        ('fade', False, False, [[num(f, 2)] for f in fades] + [[]]),
        ('molding_color', False, False, [color(s, 'molding_color') for s in scenes]),
        ('fill_color', False, False, [color(s, 'fill_color') for s in scenes]),
    ]
    if len(objects) <= 4:
        for o in objects:
            name = f'object {o["id"]} {o.get("name", "")}'.strip()
            columns.append((name, True, False, [media(s, [o['id']]) for s in scenes]))
    else:
        ids = [o['id'] for o in objects]
        columns.append(('mappings', True, False, [media(s, ids) for s in scenes]))
    columns = [c for c in columns if any(c[3])]  # empty for every scene: left out
    for c in columns:  # a picture the same as in the scene before: a line on down
        if c[1]:
            values = c[3]
            for i in range(len(values) - 1, 0, -1):
                if values[i] and values[i] == values[i - 1] and values[i] != ['blackout']:
                    values[i] = SAME
    # each column as narrow as its widest value (or name); what is left of
    # the page stays empty on the right. Too wide for the page: the media
    # columns share what the others leave, and wrap
    widths = [
        max(
            [doc.width(c[0].upper(), 6.5, True)]
            + [
                doc.width(line.strip(), size, c[2]) + _indent(line)
                for lines in c[3]
                for line in lines
            ]
        )
        for c in columns
    ]
    gap = 10
    room = doc.w - 2 * M - gap * len(columns)
    if sum(widths) > room:
        fixed = sum(w for c, w in zip(columns, widths) if not c[1])
        wraps = sum(1 for c in columns if c[1])
        share = (room - fixed) / wraps if wraps else 0
        widths = [min(w, share) if c[1] else w for c, w in zip(columns, widths)]
    xs, x = [], M
    for w in widths:
        xs.append(x)
        x += w + gap

    def head(y):
        """The column names with their top at y; returns the first row's baseline."""
        for c, x, w in zip(columns, xs, widths):
            doc.text(x, y + 6, doc.fit(c[0].upper(), 6.5, w + 2, True), 6.5, True, MUTED)
        doc.line(M, y + 10, xs[-1] + widths[-1], y + 10)
        return y + 20

    # the file's defaults
    _page_head(doc, tr('scenes.title'))
    defaults = [
        (k, _value(data[k])) for k in ('hold', 'transition', 'transition_time', 'fade') if k in data
    ]
    y = table(doc, M, 50, defaults, [90, 120], size=7.5, lead=11) if defaults else 50
    y = head(y + 10)
    for i in range(len(scenes)):
        row = [
            (
                x,
                [
                    (_indent(line), part)
                    for line in c[3][i]
                    for part in doc.wrap(line.strip(), size, w - _indent(line), c[2])
                ],
                c[2],
            )
            for c, x, w in zip(columns, xs, widths)
        ]
        h = max(len(lines) for _, lines, _ in row) * lead + 4
        if y + h > doc.h - 34:
            doc.new_page()
            _page_head(doc, tr('scenes.title'))
            y = head(44)
        if i % 2 == 0:
            doc.rect(M - 3, y - size - 2, xs[-1] + widths[-1] - M + 6, h, fill=PANEL)
        for (x, lines, bold), c in zip(row, columns):
            if c[3][i] is SAME:  # the line through the rows that keep it
                doc.line(x + 3, y - size - 2, x + 3, y - size - 2 + h, MUTED, 0.8)
                continue
            for k, (dx, line) in enumerate(lines):
                doc.text(x + dx, y + k * lead, line, size, bold)
        y += h
    doc.save()
