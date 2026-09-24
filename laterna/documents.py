"""The PDF documents of the export (laterna/export.py), with reportlab.

Layout only: export.py renders the pictures and hands them in with the
parsed config and stages. Three documents:

  config_sheet  calibration, frame shapes, look, the pretend spot, DMX
                and config.yaml itself (A4 landscape)
  run_sheet     the operator's cue list: every stage numbered as play.py's
                H label numbers it, how it is reached, what it does while
                it stands, what is on each object, where it is in the
                backup (A4 landscape)
  backup        every stage full screen, one page per picture at the
                canvas's aspect ratio, in show order (blackouts as black
                pages, one page per distinct combination of slideshow
                pictures), with an outline per stage

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
BLUE = (0.20, 0.30, 0.75)
BLUE = (0.18, 0.40, 0.56)
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
# the stages as the operator meets them


def stage_facts(stages, fades):
    """Per stage: how it is reached and what it does, as play.py plays it.

    entry: 'start' (shown when the show starts), 'key' (a step key) or
    'auto' (the previous stage's hold ran out); fade: the transition into
    it; hold: seconds it stands before moving on by itself (None = waits);
    since: (index of the stage the last key was pressed on, or None for
    the start of the show; seconds from then until this stage is fully
    in) for stages reached by themselves."""
    facts = []
    for i, st in enumerate(stages):
        prev = stages[i - 1] if i else None
        entry = 'start' if i == 0 else ('auto' if prev.get('hold') is not None else 'key')
        fade = fades[i - 1] if i else 0.0
        since = None
        if entry == 'auto':
            before = facts[-1]
            if before['since']:
                key, t = before['since']
            elif before['entry'] == 'key':
                key, t = i - 2, before['fade']  # the key pressed on the stage before it
            else:
                key, t = None, 0.0  # counted from the start of the show
            since = (key, t + prev['hold'] + fade)
        facts.append(
            {
                'entry': entry,
                'fade': fade,
                'hold': st.get('hold'),
                'since': since,
                'last': i == len(stages) - 1,
            }
        )
    return facts


def _media_label(m):
    name = (m.get('image') or m.get('video') or '').replace('\\', '/').split('/')[-1]
    stem = name.rsplit('.', 1)[0]
    if 'video' in m:
        return f'video {stem}'
    if 'slideshow' in m:
        return f'slideshow of {len(m["slideshow"])}'
    return stem


def object_contents(stage, cfg):
    """{object id: text} of what a stage shows on each object."""
    if stage.get('blackout'):
        return {o['id']: 'black' for o in cfg['objects']}
    out = {o['id']: 'fill' for o in cfg['objects']}
    for m in stage.get('mappings', []):
        for oid in m['objects']:
            out[oid] = _media_label(m)
    return out


ENTRY = {'start': ('START', MUTED), 'key': ('KEY', GREEN), 'auto': ('AUTO', ORANGE)}


def _strip(doc, y, stages, facts, highlight=()):
    """All stages as numbered cells across the page: green = reached by a
    key, orange = by itself, black bar = blackout; highlighted = drawn
    solid (the ones on this page)."""
    n = len(stages)
    gap = 2 if n <= 60 else 1
    cell = (doc.w - 2 * M - gap * (n - 1)) / n
    for j in range(n):
        x = M + j * (cell + gap)
        _, col = ENTRY[facts[j]['entry']]
        solid = j in highlight
        doc.rect(x, y, cell, 14, fill=col if solid else tuple(c * 0.25 + 0.75 for c in col))
        if cell >= 11:
            doc.text(
                x + cell / 2,
                y + 10,
                str(j + 1),
                6.5 if cell >= 15 else 5,
                solid,
                PAPER if solid else INK,
                'center',
            )
        if stages[j].get('blackout'):
            doc.rect(x, y + 11.5, cell, 2.5, fill=NIGHT)
    return y + 14


def _page_head(doc, title, subtitle):
    head = doc.show.upper()
    doc.text(M, 30, head, 8, True, GOLD)
    doc.text(M + doc.width(head, 8, True) + 10, 30, title, 8, color=MUTED)
    doc.text(doc.w - M, 30, subtitle, 8, color=MUTED, align='right')


KEYS = [
    ('Enter / space / right arrow', 'next stage (fades)'),
    ('Backspace / left arrow', 'previous stage'),
    ('same key twice within 0.5 s', 'during a fade: cut it short and step on'),
    ('>  <   (also  .  ,)', 'inside a slideshow: picture on / back'),
    ('H', 'stage label on/off (drawn in the projection itself)'),
    ('Q / Esc', 'stop, back to the menu'),
]
BLOCKS = [
    (RED, 'red', 4, 'a transition runs: one key press does nothing, two cut it short'),
    (ORANGE, 'orange', 4, 'the stage holds: it moves on by itself, the blocks count down'),
    (
        BLUE,
        'blue',
        1,
        'the hold waits in the dark: it runs only while a canvas is lit, so it starts '
        'when the desk brings the light up',
    ),
    (GREEN, 'green', 1, 'waiting for a key'),
    (
        (0.35, 0.35, 0.35),
        'grey',
        4,
        'top left, one row per slideshow or video: counts down to the next picture / the restart',
    ),
]


def run_sheet(path, cfg, stages, fades, views, backup_pages, crop_box, source, description=None):
    """The operator's cue list. views[i] = export.stage_views of stage i
    (thumb None for a blackout); backup_pages[i] = first page of stage i
    in the backup; description = the scenes file's own, and each stage's
    `description` goes under its row."""
    facts = stage_facts(stages, fades)
    n = len(stages)
    show = show_name(cfg)
    doc = Doc(path, 'Run sheet', f'{show} · run sheet · {source} · generated {today()}', show=show)
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
        if reader is None:
            doc.rect(x, y, w, h, fill=NIGHT)
            doc.text(
                x + w / 2,
                y + h / 2 + 2,
                'BLACKOUT',
                5.5 if w < 80 else 7,
                True,
                (0.5, 0.48, 0.45),
                'center',
            )
        else:
            doc.image(reader, x, y, w, h)
        return h

    # --- cover ------------------------------------------------------------
    _page_head(doc, 'Run sheet', source)
    doc.text(M, 84, 'Run sheet', 28, True)
    keys = sum(f['entry'] == 'key' for f in facts)
    doc.text(
        M,
        104,
        f'{n} stages · {keys} on a key, {n - keys - 1} by themselves · '
        f'numbered like the H label of the presentation (stage k/{n})',
        10,
        color=MUTED,
    )
    y = 118
    if description:
        y = doc.para(M, y + 4, description, 9, doc.w - 2 * M) + 2
    y = _strip(doc, y, stages, facts)
    doc.text(
        M,
        y + 11,
        'green = on a key, orange = comes by itself, black bar = blackout',
        7,
        color=MUTED,
    )

    x2 = M + 380
    top = y + 42
    y = doc.section(M, top, 'Keys')
    for k, v in KEYS:
        doc.rect(M, y - 9, 150, 13, fill=PANEL, r=2)
        doc.text(M + 5, y, k, 8, True)
        doc.text(M + 158, y, v, 8)
        y += 16
    y = doc.section(M, y + 10, 'State blocks, top right of the projection')
    for col, name, count, text in BLOCKS:
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
        doc.text(M + 30, y - 3, name, 8, True)
        y = doc.para(M + 68, y - 3, text, 8, x2 - M - 88) + 3

    y = doc.section(x2, top, 'If something goes wrong')
    y = doc.para(
        x2,
        y,
        'After Q or a restart the show always begins at stage 1. To get back to '
        'where you were: press Enter, and during every fade press Enter again within '
        'half a second - the fade is skipped and the show steps straight on. The stage '
        'number is in the first column of the cue list and in the H label.',
        8.5,
        doc.w - M - x2,
    )
    y = doc.para(
        x2,
        y + 4,
        'The first start after a change of config or scenes renders the '
        'stages first (a progress line in the picture); after that they come from the '
        'cache.',
        8.5,
        doc.w - M - x2,
    )
    y = doc.para(
        x2,
        y + 4,
        'Last resort: backup.pdf holds every stage full screen in show order '
        '(column "backup" below gives the page). Open it full screen on the projector; '
        'no fades, no spots dimming with the desk.',
        8.5,
        doc.w - M - x2,
    )
    y = doc.section(x2, y + 12, 'Columns')
    doc.para(
        x2,
        y,
        'IN: how the stage is reached and its fade. STANDS: what it does until the '
        'next step. t = seconds from the last key press until the stage is fully in. '
        'Grey text = the same as the stage before. A row of small pictures = the '
        'combinations of a slideshow, with the moment each comes up after entering.',
        8,
        doc.w - M - x2,
        color=MUTED,
    )

    # --- cue list ---------------------------------------------------------
    objects = cfg['objects']
    split = len(objects) <= 4
    X_NUM, X_PIC, PIC_W = M, M + 24, 104
    X_NAME = X_PIC + PIC_W + 10
    X_IN, X_STANDS = X_NAME + 150, X_NAME + 222
    X_OBJ = X_STANDS + 118
    X_BACKUP = doc.w - M - 30
    obj_w = (X_BACKUP - X_OBJ - 6) / (len(objects) if split else 1)
    names = {o['id']: str(o.get('name', o['id'])) for o in objects}

    def head(first, last):
        _page_head(doc, 'Run sheet', source)
        _strip(doc, 40, stages, facts, highlight=range(first, last + 1))
        doc.text(M, 80, f'Cue list · stages {first + 1}-{last + 1} of {n}', 14, True)
        y = 98
        cols = [
            (X_NUM, '#'),
            (X_PIC, 'picture'),
            (X_NAME, 'stage'),
            (X_IN, 'in'),
            (X_STANDS, 'stands'),
        ]
        if split:
            cols += [
                (X_OBJ + k * obj_w, f'{o["id"]} {names[o["id"]]}') for k, o in enumerate(objects)
            ]
        else:
            cols += [(X_OBJ, 'objects')]
        cols += [(X_BACKUP, 'backup')]
        for x, t in cols:
            doc.text(x, y, doc.fit(t.upper(), 6.5, 100, True), 6.5, True, MUTED)
        return y + 6

    def notes(i):
        text = stages[i].get('description')
        return doc.wrap(' '.join(str(text).split()), 7.5, X_BACKUP - X_NAME) if text else []

    def row_height(i):
        h = PIC_W * aspect + 10
        if notes(i):
            h += len(notes(i)) * 9.5 + 2
        if len(views[i]) > 1:
            per_line = int((X_BACKUP - X_NAME) // 76)
            lines = math.ceil(len(views[i]) / per_line)
            h += lines * (62 * aspect + 26) + 4
        if not split:
            h = max(h, 14 + 10 * len(objects))
        return h

    bottom = doc.h - 34
    pages, current, y = [], [], 104
    for i in range(n):
        h = row_height(i)
        if current and y + h > bottom:
            pages.append(current)
            current, y = [], 104
        current.append(i)
        y += h
    pages.append(current)

    for rows in pages:
        doc.new_page()
        y = head(rows[0], rows[-1])
        for i in rows:
            st, f = stages[i], facts[i]
            h = row_height(i)
            doc.line(M, y, doc.w - M, y)
            _, col = ENTRY[f['entry']]
            doc.rect(X_NUM, y + 4, 3, h - 8, fill=col)
            doc.text(X_NUM + 7, y + 17, str(i + 1), 12, True)
            picture(i, 0, X_PIC, y + 5, PIC_W)
            doc.text(
                X_NAME, y + 15, doc.fit(st.get('name', '?'), 9, X_IN - X_NAME - 8, True), 9, True
            )
            yy = y + 27
            if st.get('blackout'):
                doc.text(X_NAME, yy, 'blackout', 7.5, color=MUTED)
            # in
            label, lc = ENTRY[f['entry']]
            doc.label(X_IN, y + 7, label, lc)
            if i:
                doc.text(X_IN, y + 28, f'fade {secs(f["fade"])}', 7, color=MUTED)
            if f['since']:
                key = f['since'][0]
                doc.text(X_IN, y + 38, f't = {mmss(f["since"][1])} after', 7, color=MUTED)
                doc.text(
                    X_IN,
                    y + 47,
                    'the start' if key is None else f'the key on #{key + 1}',
                    7,
                    color=MUTED,
                )
            # stands
            if f['last']:
                stands = 'last stage: a step key does nothing'
            elif f['hold'] is not None:
                stands = f'holds {secs(f["hold"])}, then goes on by itself (a key goes earlier)'
            else:
                stands = 'waits for a key'
            shows = [m for m in st.get('mappings', []) if 'slideshow' in m]
            vids = [m for m in st.get('mappings', []) if 'video' in m]
            for m in shows:
                slot = m['hold'] + m['transition_time']
                stands += (
                    f'; slideshow: {mmss(slot)} per picture ({secs(m["hold"])} + fade '
                    f'{secs(m["transition_time"])}), {len(m["slideshow"])} pictures, loops'
                )
            if vids:
                stands += '; video loops'
            doc.para(X_STANDS, y + 15, stands, 7.5, X_OBJ - X_STANDS - 8, lead=1.3)
            # objects
            now = object_contents(st, cfg)
            before = object_contents(stages[i - 1], cfg) if i else {}
            if st.get('blackout'):
                doc.text(X_OBJ, y + 15, 'all black', 8, color=MUTED)
            elif split:
                for k, o in enumerate(objects):
                    t = now[o['id']]
                    same = before.get(o['id']) == t and not t.startswith('slideshow')
                    x = X_OBJ + k * obj_w
                    lines = doc.wrap(t.replace('-', ' '), 7.5, obj_w - 6, t.startswith('slideshow'))
                    for li, line in enumerate(lines[:3]):
                        doc.text(
                            x,
                            y + 15 + li * 9.5,
                            line,
                            7.5,
                            t.startswith('slideshow'),
                            MUTED if same or t == 'fill' else INK,
                        )
                    if same:
                        doc.text(x, y + 15 + min(len(lines), 3) * 9.5, '(stays)', 6.5, color=MUTED)
            else:
                for k, o in enumerate(objects):
                    t = now[o['id']]
                    doc.text(
                        X_OBJ,
                        y + 15 + k * 10,
                        doc.fit(f'{o["id"]}: {t}', 7.5, X_BACKUP - X_OBJ - 6),
                        7.5,
                        color=MUTED if before.get(o['id']) == t else INK,
                    )
            # backup page(s)
            first = backup_pages[i]
            last = first + len(views[i]) - 1
            doc.text(X_BACKUP, y + 15, f'p. {first}' if last == first else f'p. {first}-{last}', 8)
            # the stage's description, then the slideshow combinations
            sy = y + PIC_W * aspect + 12
            for line in notes(i):
                doc.text(X_NAME, sy + 4, line, 7.5)
                sy += 9.5
            if notes(i):
                sy += 2
            if len(views[i]) > 1:
                per_line = int((X_BACKUP - X_NAME) // 76)
                for k, v in enumerate(views[i]):
                    x = X_NAME + (k % per_line) * 76
                    ty = sy + (k // per_line) * (62 * aspect + 26)
                    th = picture(i, k, x, ty, 68)
                    changed = ', '.join(v['changed']) if v.get('changed') else ''
                    doc.text(
                        x, ty + th + 8, doc.fit(f'{k + 1}. {changed}', 6.2, 72, True), 6.2, True
                    )
                    doc.text(
                        x,
                        ty + th + 16,
                        'on entering' if k == 0 else f'from {mmss(v["t"])}',
                        6.2,
                        color=MUTED,
                    )
            y += h
        doc.line(M, y, doc.w - M, y)
    doc.save()


def backup(path, cfg, stages, views):
    """Every picture full screen, at the canvas's aspect ratio. Returns the
    first page number of every stage."""
    w, h = cfg['canvas']
    size = (w * 0.5, h * 0.5)  # 960 x 600 pt at 1920 x 1200: the pixels at 144 dpi
    doc = Doc(path, 'Backup', pagesize=size, show=show_name(cfg))
    firsts = []
    n = len(stages)
    for i, st in enumerate(stages):
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


LOOK_NOTES = {
    'molding': 'brightness of the molding',
    'fill': 'brightness of the fill (no picture)',
    'images': 'brightness of pictures and video',
    'temperature': 'colour of the light (K)',
    'white': "the projector's white (K)",
    'spot_collapse': 'spot flattens as the light dims (0 = off)',
    'plank_depth': 'parallax strip, mm (0 = off)',
}
SPOT_NOTES = {
    'type': 'cone (lamp in front) or gaussian (soft pool)',
    'strength': 'dark edge = 1 - strength',
    'position': 'lamp / centre, fraction of the frame (y 0 = bottom)',
    'aim': 'cone: where the axis hits',
    'distance': 'cone: lamp in front, x frame width',
    'angle': 'cone: half opening angle',
    'softness': 'cone: penumbra, x angle',
    'falloff': 'cone: distance falloff exponent',
    'size': 'gaussian: sigma, x frame size',
    'color': 'tint',
    'images': 'x strength on pictures',
    'fill': 'x strength on the fill',
}


def config_sheet(path, cfg, config_text, geometry, extras, dmx_info, source):
    """The calibration, the shapes and the look. geometry: per object the
    world-mm outlines (export.object_geometry); extras: the renders
    (export.config_renders); dmx_info: (settings, [(channel, labels)]) or None."""
    show = show_name(cfg)
    doc = Doc(
        path, 'Configuration', f'{show} · configuration · {source} · generated {today()}', show=show
    )
    mmpp = float(cfg['scale_mm_per_px'])
    cw, ch = cfg['canvas']
    ox, oy = (float(v) for v in cfg.get('image_offset', [0, 0]))
    look = extras['look']
    crop_box = extras['crop']

    def head(title):
        _page_head(doc, 'Configuration', source)
        doc.line(M, 38, doc.w - M, 38)
        doc.text(M, 64, title, 18, True)

    # --- overview ---------------------------------------------------------
    head('Set-up and global calibration')
    if cfg.get('description'):
        doc.text(
            M + doc.width('Set-up and global calibration', 18, True) + 14,
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
        doc.text(p[0] + 6, p[1] + 3, 'lens foot point', 6.5, color=(0.6, 0.75, 0.9))
    _dim_h(doc, v, -hw + ox, hw + ox, oy, f'picture {num(cw * mmpp, 0)} mm = {cw} px', off=26)
    _dim_v(doc, v, oy, oy + fh, -hw + ox, f'{num(fh, 0)} mm = {ch} px', off=-6)
    spans = sorted((min(p[0] for p in g['wood']), max(p[0] for p in g['wood'])) for g in geometry)
    if spans:
        _dim_h(
            doc,
            v,
            spans[0][0],
            spans[-1][1],
            oy,
            f'outer edges {num(spans[-1][1] - spans[0][0], 0)} mm',
            off=12,
        )
        low = min(min(p[1] for p in g['wood']) for g in geometry)
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            if b0 > a1:
                _dim_h(doc, v, a1, b0, low + 600, num(b0 - a1, 1), color=(0.75, 0.72, 0.66))
    doc.para(
        M,
        432,
        'Black = the whole projector picture on the frame plane at the set scale. Red dot = '
        'origin of an object (middle of its bottom plank). Light numbers between frames = the gap, '
        'wood to wood, in mm.',
        7,
        520,
        color=MUTED,
    )
    doc.para(
        M,
        458,
        'Transformation: local (frame.inner, mm) x scale, rotated, + origin = world mm; then the '
        'global rotation, - image_offset, / scale_mm_per_px = projector pixels (x from the middle, y up '
        'from the bottom edge). The inner corners are local, so the global alignment (step 3) leaves '
        'the shape calibration (step 4) intact.',
        8,
        520,
    )

    rx = M + 545
    y = doc.section(rx, 90, 'Global')
    y = table(
        doc,
        rx,
        y,
        [
            ('canvas', f'{cw} x {ch} px'),
            ('scale_mm_per_px', f'{num(mmpp, 3)} mm/px'),
            ('picture on frame plane', f'{num(cw * mmpp, 0)} x {num(fh, 0)} mm'),
            ('rotation', f'{num(cfg.get("rotation", 0), 3)} deg'),
            ('image_offset', f'[{num(ox)}, {num(oy)}] mm'),
            ('gamma', _value(cfg.get('gamma', 'none'))),
        ],
        [100, 125],
    )
    if proj:
        y = doc.section(rx, y + 10, 'Projector (parallax)')
        y = table(
            doc,
            rx,
            y,
            [
                ('position', _value(proj['position']) + ' mm'),
                ('distance', f'{num(proj["distance"])} mm'),
                ('plank_depth', f'{num(look.get("plank_depth", 0))} mm (0 = off)'),
            ],
            [100, 125],
        )
    y = doc.section(rx, y + 10, 'Objects')
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
        head=['id', 'name', 'wood mm', 'pixels x, y'],
        size=7.5,
        lead=12,
    )
    doc.para(
        rx,
        y + 6,
        'Wood = outer contour of the frame (mitred, before the rounding).',
        7,
        doc.w - M - rx,
        color=MUTED,
    )

    # --- one page per object ----------------------------------------------
    for g in geometry:
        doc.new_page()
        head(f'Object {g["id"]} · {g["name"]}')
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
        doc.text(o[0] + 5, o[1] - 10, 'origin', 6.5, color=BLUE)
        _dim_h(doc, v, lo[0], hi[0], lo[1], f'wood {num(hi[0] - lo[0], 1)} mm', off=24)
        _dim_h(
            doc,
            v,
            inner[:, 0].min(),
            inner[:, 0].max(),
            lo[1],
            f'canvas {num(np.ptp(inner[:, 0]), 1)} mm',
            off=12,
        )
        _dim_v(doc, v, lo[1], hi[1], lo[0], f'wood {num(hi[1] - lo[1], 1)} mm', off=-24)
        _dim_v(
            doc,
            v,
            inner[:, 1].min(),
            inner[:, 1].max(),
            hi[0],
            f'canvas {num(np.ptp(inner[:, 1]), 1)} mm',
            off=22,
        )
        lx = M
        for fill, t in (
            (WOOD, 'wood (outer contour, mitred)'),
            (GILT, 'projected molding (rounded)'),
            (CLOTH, 'canvas = picture area'),
            (RED, 'inner corner = calibration point'),
        ):
            doc.rect(lx, 541, 8, 8, fill=fill, stroke=INK, lw=0.3)
            lx += doc.text(lx + 12, 548, t, 7, color=MUTED) + 26

        rx = M + 470
        y = doc.section(rx, 90, 'Placement')
        (bx0, by0), (bx1, by1) = g['bbox_px']
        y = table(
            doc,
            rx,
            y,
            [
                ('origin', _value([float(c) for c in g['origin']]) + ' mm'),
                ('scale', num(g['scale'], 4)),
                ('rotation', f'{num(g["rotation"], 3)} deg'),
                ('border (plank)', f'{num(g["border"])} mm'),
                ('pixels', f'x {num(bx0, 1)}-{num(bx1, 1)}, y {num(by0, 1)}-{num(by1, 1)}'),
            ],
            [85, 200],
        )
        y = doc.section(rx, y + 10, 'Inner corners (frame.inner, local mm)')
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
            head=['#', 'x', 'y', 'projector px'],
            size=size,
            lead=lead,
        )
        seg = np.linalg.norm(np.diff(np.vstack([inner, inner[:1]]), axis=0), axis=1)
        doc.para(
            rx,
            y + 8,
            f'Edges of the inner polygon: {num(seg.min(), 1)} to {num(seg.max(), 1)} mm, '
            f'{len(inner)} corners. In step 4 (page corners) < > selects a corner, the arrows move '
            'it 1 mm (Shift 10).',
            7.5,
            doc.w - M - rx,
            color=MUTED,
        )

    # --- molding, light and look -------------------------------------------
    doc.new_page()
    head('Molding and light')
    detail = extras['molding_detail']
    dw = 400
    dh = dw * detail.shape[0] / detail.shape[1]
    doc.image(jpeg(detail, 1000), M, 80, dw, dh)
    doc.para(
        M,
        80 + dh + 12,
        'Render without pictures (the fill) at full light, top of the largest frame: the '
        'profile, the fixed light direction and the shadow the molding throws on the canvas.',
        7,
        dw,
        color=MUTED,
    )
    headroom = extras['headroom']
    doc.para(
        M,
        80 + dh + 36,
        f'Brightness above 1 asks for more light than the projector has, so the '
        f'render is stored {num(headroom, 2)} x darker and the lamps give that back: the clipping comes '
        'after the dimming, and a dimmed spot keeps its gradient. At full light the picture is the '
        'same.',
        8,
        dw,
    )
    rx = M + 430
    y = 90
    for title, node, notes in (
        ('border', cfg.get('border') or {}, {}),
        ('light', cfg.get('light') or {}, {}),
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
                [(k, _value(x), notes.get(k, '')) for k, x in node.items()],
                [80, 80, 180],
                size=7.8,
                lead=12,
            )
            + 10
        )

    # --- the spot ---------------------------------------------------------
    doc.new_page()
    head('The pretend spot: how the light falls')
    iw = (doc.w - 2 * M - 20) / 3
    asp = (crop_box[3] - crop_box[1]) / (crop_box[2] - crop_box[0])
    for k, (img, t) in enumerate(
        (
            (extras['white_spot'], 'with the spot, as set'),
            (extras['gain_map'], 'spot strength on the picture (false colour)'),
            (extras['white_flat'], 'without the spot (strength 0)'),
        )
    ):
        x = M + k * (iw + 10)
        doc.image(jpeg(crop(img, crop_box), 900), x, 78, iw, iw * asp)
        doc.text(x, 78 + iw * asp + 10, t, 7, color=MUTED)
    doc.text(
        M,
        78 + iw * asp + 21,
        'Every canvas white, no pictures: only the light. Each frame gets its own '
        'spot, placed relative to its own outline, so all frames get the same fan of light.',
        7,
        color=MUTED,
    )

    top = 78 + iw * asp + 36
    rx = M
    y = top + 7
    for title, spot in (
        ('spot (picture and fill)', look.get('spot')),
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
        rows = [(k, _value(spot[k]), SPOT_NOTES.get(k, '')) for k in keys if k in spot]
        table(doc, rx, y, rows, [60, 80, 220], size=7.5, lead=11.5)
        rx += 390
        y = top + 7

    # --- the spot on a stage -----------------------------------------------
    if extras.get('stage_spot') is not None:
        doc.new_page()
        head(f'The spot on a stage: {extras["stage_name"]}')
        iw = (doc.w - 2 * M - 10) / 2
        for k, (img, t) in enumerate(
            (
                (extras['stage_spot'], 'with the spot, as in the show'),
                (extras['stage_flat'], 'without the spot (strength 0)'),
            )
        ):
            x = M + k * (iw + 10)
            doc.image(jpeg(crop(img, crop_box), 1200), x, 80, iw, iw * asp)
            doc.text(x, 80 + iw * asp + 12, t, 8, True)
        spot = look.get('spot') or {}
        doc.para(
            M,
            80 + iw * asp + 32,
            f'The spot brightens each frame around its aim point and lets the '
            f'edges fall back to {num(1 - float(spot.get("strength", 0)), 2)}, so the frames look lit by '
            'the theatre light. As the desk dims the light the pool flattens first '
            f'(spot_collapse {num(look.get("spot_collapse", 0), 2)}); at full light nothing changes. '
            f'The colour comes from look.temperature ({num(look.get("temperature", 6500), 0)} K) or the '
            "desk's cct channel.",
            8.5,
            doc.w - 2 * M,
        )

    # --- DMX ----------------------------------------------------------------
    if dmx_info:
        settings, channels = dmx_info
        doc.new_page()
        head('DMX')
        y = doc.section(M, 90, 'Input')
        rows = [
            (k, _value(settings[k]))
            for k in ('source', 'universe', 'port', 'address', 'cct', 'smooth', 'start')
            if k in settings
        ]
        y = table(doc, M, y, rows, [80, 300])
        y = doc.section(M, y + 12, 'Channels')
        address = int(settings.get('address', 1))
        rows = [
            (f'{address + off - 1}', f'offset {off}', ' + '.join(labels))
            for off, labels in channels
        ]
        y = table(doc, M, y, rows, [60, 60, 300], head=['DMX channel', 'offset', 'function'])
        doc.para(
            M,
            y + 10,
            'Channel = address + offset - 1. Objects sharing a channel dim together. '
            'cct: 128 = look.temperature, 0-128 from the warm end, 128-255 to the cool end '
            '(dmx.cct). start full: a channel is full (cct 128) until the desk changes its '
            'value; start desk: the desk rules from its first frame. '
            'smooth: time constants up / down in seconds.',
            8,
            420,
            color=MUTED,
        )

    # --- config.yaml itself -------------------------------------------------
    lines = config_text.rstrip('\n').split('\n')
    per_col = 64
    for p0 in range(0, len(lines), per_col * 2):
        doc.new_page()
        head('Appendix: config.yaml' + (' (continued)' if p0 else ''))
        for c in range(2):
            for k, line in enumerate(lines[p0 + c * per_col : p0 + (c + 1) * per_col]):
                doc.text(
                    M + c * 390, 84 + k * 7.5, doc.fit(line, 6.2, 380, mono=True), 6.2, mono=True
                )
    doc.save()
