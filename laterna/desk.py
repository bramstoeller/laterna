#!/usr/bin/env python
"""DMX: the light desk, step 8.

Two pages, Tab switches:

  channels the desk as config.yaml `dmx:` has it, to set up: the source
           and its signal, then the settings, each with its value live
           where it is a channel: source, address, master, cct, scene, per frame
           its canvas and frame channel (0 = none), start and the dimmer
           curve. up / down picks one, left / right changes it (Shift: x10),
           at once (the desk follows; an address that runs past 512 is
           refused); S saves what changed into config.yaml's `dmx:`, T
           goes back to the values at the start
  demo     the scenes (as Look shows them, laterna/look.py) under the
           desk's channels, with the faders on screen (laterna/faders.py):
           they follow the desk and can be dragged, or selected with 1..9
           and nudged with up/down (held down the key repeats, so the
           fader slides; with Shift five times as fast); left / right
           steps through the scenes

Keys:
  Tab            next page
  up / down      a setting (channels) / move the fader (demo, Shift: x5)
  left / right   change the setting (channels, Shift: x10) / scene (demo)
  1..9           select a fader (demo)
  S / T          save the settings / back to the start (channels)
  H              help on/off
  Q / ESC        quit (back to the menu)
"""

import argparse
import collections
import copy
import time

import pygame

from . import calibration, dmx, faders, lamps, look, render, ui

PAGES = ('channels', 'demo')
REPEAT = (300, 30)  # ms before the first key repeat, ms between: ~33 values/s
TEXT = (200, 190, 170)
DIM = (130, 120, 105)
HAND = faders.HAND
SELECTED = faders.SELECTED
STARTS = ('full', 'desk')
CURVE = (0.1, 0.5, 4.0)  # step, lowest, highest


class Editor:
    """The desk's settings as the channels page edits them: source,
    address, start, curve and the channel map (0 = no channel). The
    channel rows come in the order of their channels, as the faders on the
    desk; the ones without a channel last."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.names = {o['id']: o.get('name', o['id']) for o in cfg['objects']}
        self.values = self._from(dmx.settings(cfg))
        self.start = copy.deepcopy(self.values)
        self.head = [
            ('source', ('source',), 'choice', dmx.SOURCES),
            ('address', ('address',), 'int', (1, 512)),
        ]
        self.channel_rows = [
            ('master', ('master',), 'channel', None),
            ('cct', ('cct',), 'channel', None),
            ('scene (1 = the first)', ('scene',), 'channel', None),
        ]
        for oid, name in self.names.items():
            for function in ('canvas', 'frame'):
                self.channel_rows.append(
                    (f'{name} {function}', ('objects', oid, function), 'channel', None)
                )
        self.tail = [
            ('start', ('start',), 'choice', STARTS),
            ('dimmer curve', ('curve',), 'float', CURVE),
        ]

    @property
    def rows(self):
        """The rows as the page shows them: source and address, the channels
        in channel order (none last; equal ones as config.yaml lists them),
        then start and the curve."""
        order = sorted(
            range(len(self.channel_rows)),
            key=lambda i: (
                self.get(self.channel_rows[i][1]) == 0,
                self.get(self.channel_rows[i][1]),
                i,
            ),
        )
        return self.head + [self.channel_rows[i] for i in order] + self.tail

    def index(self, path):
        """Where the row of `path` stands now."""
        return next(i for i, row in enumerate(self.rows) if row[1] == path)

    @staticmethod
    def _from(s):
        ch = s['channels']
        return {
            'source': s['source'],
            'address': s['address'],
            'master': ch['master'] or 0,
            'cct': ch['cct'] or 0,
            'scene': ch['scene'] or 0,
            'objects': {oid: dict(spec) for oid, spec in ch['objects'].items()},
            'start': s['start'],
            'curve': float(s['curve']),
        }

    def get(self, path, values=None):
        node = self.values if values is None else values
        for key in path[:-1]:
            node = node[key]
        return node.get(path[-1], 0) if path[0] == 'objects' else node[path[-1]]

    def _set(self, values, path, value):
        node = values
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    @staticmethod
    def channels(values):
        """The channel map as config.yaml writes it (no zeros)."""
        out = {k: values[k] for k in ('master', 'cct', 'scene') if values[k]}
        out['objects'] = {
            oid: {f: n for f, n in spec.items() if n} for oid, spec in values['objects'].items()
        }
        out['objects'] = {oid: spec for oid, spec in out['objects'].items() if spec}
        return out

    def dmx(self, values=None):
        """The cfg['dmx'] these values give (the other keys as they are)."""
        v = self.values if values is None else values
        out = dict(self.cfg.get('dmx') or {})
        out.update({k: v[k] for k in ('source', 'address', 'start', 'curve')})
        out['channels'] = self.channels(v)
        return out

    def change(self, index, steps):
        """Step row `index` by `steps`; returns the new cfg['dmx'], or a
        ValueError's text when the desk refuses it (nothing changes then)."""
        _, path, kind, spec = self.rows[index]
        value = self.get(path)
        if kind == 'choice':
            value = spec[(spec.index(value) + (1 if steps > 0 else -1)) % len(spec)]
        elif kind == 'float':
            step, lo, hi = spec
            value = round(min(hi, max(lo, value + steps * step)), 2)
        else:
            lo, hi = spec if kind == 'int' else (0, 512)
            value = min(hi, max(lo, int(value) + steps))
        candidate = copy.deepcopy(self.values)
        self._set(candidate, path, value)
        new = self.dmx(candidate)
        try:
            dmx.settings({**self.cfg, 'dmx': new})
        except ValueError as e:
            return str(e)
        self.values = candidate
        return new

    def changed(self):
        """{dmx key: value} of what differs from the start (for saving)."""
        out = {
            k: self.values[k]
            for k in ('source', 'address', 'start', 'curve')
            if self.values[k] != self.start[k]
        }
        if self.channels(self.values) != self.channels(self.start):
            out['channels'] = self.channels(self.values)
        return out


def channel_lines(desk, editor, selected=None):
    """The channels page as [(text, colour)]: the source and its signal,
    then the settings (the editor's rows, the one `selected` marked), a
    channel with its DMX number and live value."""
    s = desk.settings
    lines = [('DMX in (config.yaml dmx:)', TEXT)]
    lines.append(('  ' + '   '.join(desk.status_lines()[:1]), DIM))
    port = {'sacn': f'universe {s["universe"]}', 'artnet': f'universe {s["universe"]}'}
    port['enttec'] = f'port {s["port"]}'
    if s['source'] in port:
        lines.append(('  ' + port[s['source']], DIM))
    lines.append(('', DIM))
    values = desk.faders()
    for i, (label, path, kind, _) in enumerate(editor.rows):
        value = editor.get(path)
        mark = '>' if i == selected else ' '
        text = f'{mark} {label:<28s} '
        colour = SELECTED if i == selected else TEXT
        if kind == 'channel':
            if value:
                live = values.get(value)
                hand = value in desk.override
                text += f'{value:>3d}   DMX {s["address"] + value - 1:>3d}'
                text += f'   {live:>3.0f}' if live is not None else ''
                text += '  by hand' if hand else ''
                if hand and i != selected:
                    colour = HAND
            else:
                text += '  -   (none)'
        elif kind == 'float':
            text += f'{value:g}'
        else:
            text += f'{value}'
        lines.append((text, colour))
    return lines


def run(
    screen=None, config='config.yaml', scenes_path='scenes.yaml', supersample=3, dmx_source=None
):
    """Run the app; with a screen provided, reuse it (menu mode).
    `dmx_source` overrides dmx.source (the --dmx flag)."""
    cfg = render.load_config(config)
    cfg['look'] = render.look_settings(cfg)
    if dmx_source:
        cfg['dmx'] = {**(cfg.get('dmx') or {}), 'source': dmx_source}
    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'DMX — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'DMX — {ui.APP_NAME}')
    font = ui.help_font()
    desk = dmx.Desk(cfg)  # after the screen: from here on the finally below frees it
    fader_font = pygame.font.SysFont('monospace', 16)
    panel = faders.Faders(desk, fader_font)
    panel.select(0)
    pygame.key.set_repeat(*REPEAT)
    editor = Editor(cfg)

    page = 0
    selected = 0  # the setting on the channels page
    message = ''
    show_help = True
    # the demo renders the scenes, once it is first shown
    demo = {'renderer': None, 'scenes': None, 'lights': None, 'idx': 0, 'surface': None}
    ticks = collections.deque(maxlen=60)  # (time, lamps ms) of the last frames drawn

    def render_scene():
        if demo['renderer'] is None:
            screen.fill((0, 0, 0))
            ui.draw_help(screen, font, ['preparing the scenes...'])
            pygame.display.flip()
            demo['scenes'] = look.load_views(scenes_path, cfg)
            demo['renderer'] = render.SceneRenderer(cfg, ss=supersample)
            demo['lights'] = lamps.Lamps(cfg)
        scene = demo['scenes'][demo['idx']]
        demo['surface'] = demo['renderer'].render(scene)
        demo['molding'] = demo['renderer'].render_molding(scene)
        ticks.clear()

    def set_page(new):
        nonlocal page
        page = new
        if PAGES[page] == 'demo':
            if demo['surface'] is None:
                render_scene()
            panel.visible = True
        else:
            panel.visible = False

    def draw():
        if PAGES[page] == 'demo':
            t0 = time.perf_counter()
            demo['lights'].light(screen, demo['surface'], demo['molding'], desk.levels())
            ticks.append((t0, (time.perf_counter() - t0) * 1000.0))
            top = panel.draw(screen)
            lines = []
            if show_help:
                scene = demo['scenes'][demo['idx']]
                lines.append(
                    f'[demo]  view {demo["idx"] + 1}/{len(demo["scenes"])}: {scene.get("name", "?")}'
                )
                if len(ticks) > 1 and ticks[-1][0] > ticks[0][0]:
                    fps = (len(ticks) - 1) / (ticks[-1][0] - ticks[0][0])
                    ms = sum(m for _, m in ticks) / len(ticks)
                    lines.append(f'{fps:.0f} fps   lamps {ms:.1f} ms per frame')
                lines += desk.status_lines()
                lines.append(
                    '1-9 fader  up/down move (Shift x5)  left/right scene  Tab page  H help  Q quit'
                )
            if lines:
                ui.draw_help(screen, font, lines, top=top + 20)
        else:
            screen.fill((0, 0, 0))
            desk.frame()  # the filter runs on
            y = 30
            for text, colour in channel_lines(desk, editor, selected):
                screen.blit(font.render(text, True, colour), (30, y))
                y += 28
            if show_help:
                unsaved = '   * unsaved changes *' if editor.changed() else ''
                hints = [
                    'up/down setting  left/right change (Shift x10)  S save  T reset' + unsaved,
                    'Tab demo (faders)  H help  Q quit',
                ] + ([message] if message else [])
                for k, hint in enumerate(hints):
                    screen.blit(font.render(hint, True, DIM), (30, y + 20 + 28 * k))
        pygame.display.flip()

    def rebuild(old, new_dmx):
        """A desk (and faders) for the edited settings; the receiver goes on
        unless the source changed, so a widget is not opened per key."""
        same = new_dmx['source'] == old.settings['source']
        cfg['dmx'] = new_dmx
        new = dmx.Desk(cfg, receiver=old.receiver if same else None)
        if not same:
            old.close()
        fresh = faders.Faders(new, fader_font)
        fresh.select(0)
        fresh.visible = panel.visible
        return new, fresh

    def save(editor):
        """What changed into config.yaml's dmx section (the file as it is
        on disk, nothing else of it changes); a message."""
        changes = editor.changed()
        if not changes:
            return 'nothing changed'
        on_disk = render.load_config(config)
        on_disk['dmx'] = {**(on_disk.get('dmx') or {}), **changes}
        calibration.save_config(on_disk, config)
        editor.start = copy.deepcopy(editor.values)
        return f'saved to {config}: {", ".join(changes)}'

    try:
        set_page(0)
        running = True
        while running:
            event = pygame.event.wait(16)  # the values move: redraw every 16 ms
            if event.type == pygame.QUIT:
                running = False
            elif panel.visible and panel.handle(event):
                pass
            elif event.type == pygame.KEYDOWN:
                steps = 5 if event.mod & pygame.KMOD_SHIFT else 1
                on_channels = PAGES[page] == 'channels'
                if event.key in ui.QUIT_KEYS:
                    running = False
                elif event.key == pygame.K_TAB:
                    set_page((page + 1) % len(PAGES))
                elif event.key == pygame.K_h:
                    show_help = not show_help
                elif on_channels and event.key in (pygame.K_UP, pygame.K_DOWN):
                    step = -1 if event.key == pygame.K_UP else 1
                    selected = (selected + step) % len(editor.rows)
                elif on_channels and event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                    big = 10 if event.mod & pygame.KMOD_SHIFT else 1
                    path = editor.rows[selected][1]
                    new = editor.change(selected, big if event.key == pygame.K_RIGHT else -big)
                    selected = editor.index(path)  # a channel row moves with its number
                    if isinstance(new, str):
                        message = new  # refused: the desk stays as it was
                    else:
                        message = ''
                        desk, panel = rebuild(desk, new)
                elif on_channels and event.key == pygame.K_s:
                    message = save(editor)
                elif on_channels and event.key == pygame.K_t:
                    path = editor.rows[selected][1]
                    editor.values = copy.deepcopy(editor.start)
                    selected = editor.index(path)
                    desk, panel = rebuild(desk, editor.dmx())
                    message = 'back to the values at the start'
                elif on_channels:
                    pass
                elif event.key in look.SELECT_KEYS:
                    panel.select(look.SELECT_KEYS[event.key])
                elif event.key in (pygame.K_UP, pygame.K_DOWN):
                    panel.nudge(steps if event.key == pygame.K_UP else -steps)
                elif event.key in (pygame.K_LEFT, pygame.K_RIGHT) and PAGES[page] == 'demo':
                    step = 1 if event.key == pygame.K_RIGHT else -1
                    idx = min(max(demo['idx'] + step, 0), len(demo['scenes']) - 1)
                    if idx != demo['idx']:
                        demo['idx'] = idx
                        render_scene()
            draw()
    finally:  # also on an error (a text that does not fit, ...): free the desk
        desk.close()
        pygame.key.set_repeat()
    pygame.mouse.set_visible(False)
    if standalone:
        pygame.quit()


def self_test(config, scenes_path):
    """Headless check: the channels page's lines with the demo desk."""
    cfg = render.load_config(config)
    cfg['look'] = render.look_settings(cfg)
    cfg['dmx'] = {**(cfg.get('dmx') or {}), 'source': 'demo'}
    desk = dmx.Desk(cfg)
    time.sleep(0.2)
    editor = Editor(cfg)
    for text, _ in channel_lines(desk, editor, 0):
        print(text)
    assert isinstance(editor.change(1, 1), dict) and editor.changed() == {
        'address': editor.start['address'] + 1
    }
    desk.close()
    print('self-test ok')


def main():
    ap = argparse.ArgumentParser(description="The light desk's channels live, and a demo")
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--scenes', default='scenes.yaml')
    ap.add_argument('--supersample', type=int, default=3)
    ap.add_argument(
        '--dmx', choices=dmx.SOURCES, help='override dmx.source (demo = a scripted desk)'
    )
    ap.add_argument('--test', action='store_true', help='self-test: the channels page, no display')
    args = ap.parse_args()
    if args.test:
        self_test(args.config, args.scenes)
        return
    run(
        config=args.config,
        scenes_path=args.scenes,
        supersample=args.supersample,
        dmx_source=args.dmx,
    )


if __name__ == '__main__':
    main()
