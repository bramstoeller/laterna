#!/usr/bin/env python
"""DMX: the light desk, step 7.

Two pages, Tab switches:

  channels the desk as config.yaml `dmx:` has it: the source and its
           signal, the fixture's channels (absolute, offset, function)
           with their values live
  demo     the scenes (as Look shows them, laterna/look.py) under the
           desk's channels, with the faders on screen (laterna/faders.py):
           they follow the desk and can be dragged, or selected with 1..9
           and nudged with up/down (on the channels page too, unseen) (held down the key repeats, so the
           fader slides; with Shift five times as fast); left / right
           steps through the scenes

Nothing is saved here: it is there to check the cable and the patch and
to try the faders, from the desk, the keys or the mouse.

Keys:
  Tab            next page
  1..9           select a fader (demo)
  up / down      move it (Shift: x5)
  left / right   previous / next scene (demo)
  H              help on/off
  Q / ESC        quit (back to the menu)
"""

import argparse
import collections
import time

import pygame

from . import dmx, faders, lamps, look, render, ui

PAGES = ('channels', 'demo')
REPEAT = (300, 30)  # ms before the first key repeat, ms between: ~33 values/s
TEXT = (200, 190, 170)
DIM = (130, 120, 105)
HAND = faders.HAND


def channel_lines(desk):
    """The channels page as [(text, colour)]: the source, its signal and
    the fixture's channels live."""
    s = desk.settings
    lines = []

    lines.append(('DMX in (config.yaml dmx:)', TEXT))
    lines.append(('  ' + '   '.join(desk.status_lines()[:1]), DIM))
    parts = [f'source {s["source"]}']
    if s['source'] in ('sacn', 'artnet'):
        parts.append(f'universe {s["universe"]}')
    elif s['source'] == 'enttec':
        parts.append(f'port {s["port"]}')
    parts += [f'address {s["address"]}', f'start {s["start"]}', f'curve {s["curve"]:g}']
    lines.append(('  ' + '  '.join(parts), DIM))
    lines.append(('', DIM))
    lines.append(('  channel  offset  function                 value', TEXT))
    values = desk.faders()
    for offset in desk.offsets:
        label = ' '.join(p for p in desk.offset_labels[offset] if p)
        hand = offset in desk.override
        lines.append(
            (
                f'  {s["address"] + offset - 1:>7d}  {offset:>6d}  {label:<24s} '
                f'{values[offset]:>5.0f}' + ('  by hand' if hand else ''),
                HAND if hand else TEXT,
            )
        )
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
    panel = faders.Faders(desk, pygame.font.SysFont('monospace', 16))
    panel.select(0)
    pygame.key.set_repeat(*REPEAT)

    page = 0
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
            for text, colour in channel_lines(desk):
                screen.blit(font.render(text, True, colour), (30, y))
                y += 28
            if show_help:
                hint = 'Tab demo (faders)  H help  Q quit'
                screen.blit(font.render(hint, True, DIM), (30, y + 20))
        pygame.display.flip()

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
                if event.key in ui.QUIT_KEYS:
                    running = False
                elif event.key == pygame.K_TAB:
                    set_page((page + 1) % len(PAGES))
                elif event.key == pygame.K_h:
                    show_help = not show_help
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
    for text, _ in channel_lines(desk):
        print(text)
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
