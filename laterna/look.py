#!/usr/bin/env python
"""Look: brightness per layer and the pretend spotlights (`look` in config.yaml).

Shows the stages from scenes.yaml as the presentation renders them (no
fades; video polygons stay black here) and lets you tune, live. The
first view has every canvas white (the light itself, on a blank
canvas). Blackout stages are skipped (nothing to look at) and a stage
with slideshows is shown once per slide: view k has every slideshow of
the stage on its k-th image (shorter ones wrap), so each image gets
seen. Tune:

  brightness of the molding, of the fill colours and of the images/videos
  colour temperature of the light (K; 3200 = tungsten warm); not in the
  render but applied by the lamps, like the desk's cct channel, which
  defaults to it; and the projector's own white (K), what the light's
  colour is made relative to (6500 unless the projector is set warmer)
  the spot on every frame (the frames are not lit by real spots because of
  the projection, so we pretend): the picture spot (`spot`: a gaussian pool
  or a cone lamp close to the frame) and, when configured, the molding's
  own spot (`molding_spot`). Settings come in pages, Tab cycles them.

The last page, `desk`, shows the view under the light desk's channels
(config.yaml `dmx:`, laterna/dmx.py) with the faders on screen (laterna/faders.py):
they follow the desk and can be dragged, or selected with 1..9 and
nudged with up/down (held down the key repeats, so the fader slides;
with Shift five times as fast). Nothing on that page is saved: it is
there to try
the faders, from the desk, the keys or the mouse, and it shows no menu,
only the faders. The other pages show the base settings, without the
desk.

Keys:
  1..9, 0        select a setting on the current page (see the overlay);
                 on the desk page a fader
  Tab            next page (look / picture spot / molding spot / desk)
  up / down      adjust the selected setting  (with Shift: x5); on the desk
                 page the key repeats while held, so a fader slides
  left / right   previous / next stage
  T              reset all settings to the values at startup
  S              save to config.yaml
  R              reload config.yaml and rebuild the molding (after editing
                 `light`/`border` by hand); unsaved look changes are lost

Other:
  H  help overlay on/off
  Q / ESC  quit (back to the menu when started from main.py)
"""

import argparse
import copy

import numpy as np
import pygame

from . import calibration, dmx, faders, lamps, render, ui

# Settings come in pages (Tab cycles them): label, path into cfg['look'],
# step, minimum, maximum. Keys 1..9 and 0 select within the page.
PAGE_LOOK = (
    'look',
    [
        ('molding brightness', ('molding',), 0.05, 0.0, 3.0),
        ('fill brightness', ('fill',), 0.05, 0.0, 3.0),
        ('image brightness', ('images',), 0.05, 0.0, 3.0),
        ('colour temperature (K)', ('temperature',), 100.0, 1500.0, 10000.0),
        ('projector white (K)', ('white',), 100.0, 3000.0, 10000.0),
        ('pool flattens when dim', ('spot_collapse',), 0.05, 0.0, 1.0),
        ('spot strength', ('spot', 'strength'), 0.05, 0.0, 1.0),
        ('spot on images', ('spot', 'images'), 0.05, 0.0, 1.0),
        ('spot on fill', ('spot', 'fill'), 0.05, 0.0, 1.0),
        ('plank depth (mm, 0 = off)', ('plank_depth',), 1.0, 0.0, 50.0),
    ],
)


def _spot_page(name, key, spot):
    """Geometry settings of one spot dict (cfg['look'][key])."""
    if spot.get('type', 'gaussian') == 'cone':
        return (
            f'{name} (cone)',
            [
                ('strength', (key, 'strength'), 0.05, 0.0, 1.0),
                ('lamp x (0.5 = centre)', (key, 'position', 0), 0.05, -1.0, 2.0),
                ('lamp height (1 = top)', (key, 'position', 1), 0.05, -1.0, 2.0),
                ('aim x', (key, 'aim', 0), 0.05, -1.0, 2.0),
                ('aim height', (key, 'aim', 1), 0.05, -1.0, 2.0),
                ('distance (x width)', (key, 'distance'), 0.05, 0.05, 5.0),
                ('beam half-angle (deg)', (key, 'angle'), 1.0, 1.0, 89.0),
                ('softness', (key, 'softness'), 0.05, 0.0, 1.0),
                ('falloff', (key, 'falloff'), 0.1, 0.0, 3.0),
            ],
        )
    return (
        f'{name} (gaussian)',
        [
            ('strength', (key, 'strength'), 0.05, 0.0, 1.0),
            ('centre x (0.5 = centre)', (key, 'position', 0), 0.05, -1.0, 2.0),
            ('centre height (1 = top)', (key, 'position', 1), 0.05, -1.0, 2.0),
            ('width (sigma / width)', (key, 'size', 0), 0.05, 0.05, 3.0),
            ('spread (sigma / height)', (key, 'size', 1), 0.05, 0.05, 3.0),
        ],
    )


PAGE_DESK = ('desk', [])  # no settings: the faders, and the desk's levels applied


def pages(look):
    """The settings pages for a (completed) look dict; the desk page last."""
    out = [PAGE_LOOK, _spot_page('picture spot', 'spot', look['spot'])]
    if look.get('molding_spot'):
        out.append(_spot_page('molding spot', 'molding_spot', look['molding_spot']))
    out.append(PAGE_DESK)
    return out


# keys 1..9 select settings 0..8, key 0 the tenth
SELECT_KEYS = {getattr(pygame, f'K_{i}'): (i - 1) % 10 for i in range(0, 10)}
SELECT_KEYS.update({getattr(pygame, f'K_KP_{i}'): (i - 1) % 10 for i in range(0, 10)})


def _slot(look, path):
    """(container, key) for a settings path inside the look dict."""
    node = look
    for key in path[:-1]:
        node = node[key]
    return node, path[-1]


def get_value(look, setting):
    node, key = _slot(look, setting[1])
    return float(node[key])


def adjust(look, setting, steps):
    """Step a setting by `steps` increments, clamped to its range."""
    _, path, step, lo, hi = setting
    node, key = _slot(look, path)
    node[key] = round(min(hi, max(lo, float(node[key]) + steps * step)), 4)


WHITE_VIEW = {'name': 'white canvases', 'fill_color': (255, 255, 255), 'mappings': []}


def views(stages):
    """The stages as the look app shows them: first every canvas white
    (the light on a blank canvas), then the stages with blackouts left
    out and a stage with slideshows expanded to one view per slide (the
    k-th image of every slideshow in the stage; video items keep their
    `video:` and stay black, like video mappings). Each view is a stage
    dict render() accepts."""
    out = [WHITE_VIEW]
    for stage in stages:
        if stage.get('blackout'):
            continue
        shows = [m for m in stage.get('mappings', []) if 'slideshow' in m]
        if not shows:
            out.append(stage)
            continue
        count = max(len(m['slideshow']) for m in shows)
        for k in range(count):
            mappings = []
            for m in stage['mappings']:
                if 'slideshow' in m:
                    item = m['slideshow'][k % len(m['slideshow'])]
                    m = {
                        **{
                            key: v
                            for key, v in m.items()
                            if key not in ('slideshow', 'hold', 'transition', 'transition_time')
                        },
                        ('video' if render.is_video_path(item) else 'image'): item,
                    }
                mappings.append(m)
            out.append(
                {
                    **stage,
                    'name': f'{stage.get("name", "?")} [{k + 1}/{count}]',
                    'mappings': mappings,
                }
            )
    return out


def load_views(scenes_path, cfg):
    stages, _ = render.parse_stages(render.load_scenes(scenes_path), cfg)
    return views(stages)


def self_test(cfg, scenes_path):
    """Headless check: one render plus the edit functions; does not save."""
    cfg['look'] = render.look_settings(cfg)
    stages = load_views(scenes_path, cfg)
    look_page, spot_page = pages(cfg['look'])[:2]
    adjust(cfg['look'], spot_page[1][0], 7)  # spot strength +0.35
    adjust(cfg['look'], look_page[1][0], -2)  # molding brightness -0.1
    renderer = render.StageRenderer(cfg, ss=2)
    # what the lamps do at full light: the headroom back, in the light's colour
    gain = render.headroom_gain(cfg) * render.white_gain(
        cfg['look']['temperature'], cfg['look']['white']
    )

    def lit(image):
        return np.clip(image.astype(np.float32) * gain, 0, 255).astype(np.uint8)

    render.save_png(lit(renderer.render(stages[min(3, len(stages) - 1)])), 'renders/look.png')
    render.save_png(lit(renderer.render(stages[0])), 'renders/look-white.png')
    print('self-test ok (written: renders/look.png, renders/look-white.png)')


def run(
    screen=None, config='config.yaml', scenes_path='scenes.yaml', supersample=3, dmx_source=None
):
    """Run the app; with a screen provided, reuse it (menu mode).
    `dmx_source` overrides dmx.source for the desk page (the --dmx flag)."""
    cfg = render.load_config(config)
    cfg['look'] = render.look_settings(cfg)  # complete, so every setting exists
    snap = copy.deepcopy(cfg['look'])
    stages = load_views(scenes_path, cfg)
    # the desk for the desk page; a --dmx override stays out of cfg, so S
    # never saves it
    desk = dmx.Desk(
        {**cfg, 'dmx': {**(cfg.get('dmx') or {}), 'source': dmx_source}} if dmx_source else cfg
    )

    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Look — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Look — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    font = ui.help_font()
    # every step re-renders (~1 s) on the look pages: no key repeat there.
    # The desk page only moves a fader, so there the key repeats (set_page)
    pygame.key.set_repeat()
    REPEAT = (300, 30)  # ms before the first repeat, ms between: ~33 values/s
    lights = lamps.Lamps(cfg)
    panel = faders.Faders(desk, pygame.font.SysFont('monospace', 16))

    def build_renderer():
        screen.fill((0, 0, 0))
        ui.draw_help(screen, font, ['preparing the molding...'])
        pygame.display.flip()
        return render.StageRenderer(cfg, ss=supersample)

    renderer = build_renderer()

    page, selected = 0, 0
    idx = 0  # the white canvases first
    dirty = False
    show_help = True
    message = ''
    surface = molding = None

    def on_desk_page():
        all_pages = pages(cfg['look'])
        return all_pages[page % len(all_pages)] is PAGE_DESK

    def rerender():
        nonlocal surface, molding
        renderer.apply_look()
        desk.kelvin = float(cfg['look']['temperature'])  # the desk's cct 128
        lights.white = float(cfg['look']['white'])
        lights.collapse = float(cfg['look']['spot_collapse'])
        surface = renderer.render(stages[idx])
        molding = renderer.render_molding(stages[idx])

    def set_page(new):
        """Switch pages; the desk page brings the faders and the mouse,
        with the first fader selected for the keys and key repeat on (hold
        up/down to slide a fader)."""
        nonlocal page, selected
        page, selected = new, 0
        if on_desk_page() != panel.visible:
            panel.toggle()
        if on_desk_page():
            panel.select(0)
            pygame.key.set_repeat(*REPEAT)
        else:
            pygame.key.set_repeat()

    def show():
        # the base pages: full light at the look's colour temperature; the
        # desk page: whatever the desk (or the faders) ask
        levels = desk.levels() if on_desk_page() else dmx.Levels.full(desk.kelvin, desk.ids)
        lights.light(screen, surface, molding, levels)
        top = panel.draw(screen) if panel.visible else 0
        if on_desk_page():
            # the faders speak for themselves: no menu here, only a message
            if message:
                ui.draw_help(screen, font, [message], top=top + 20)
        elif show_help:
            lines = [
                f'view {idx + 1}/{len(stages)}: {stages[idx].get("name", "?")}'
                + ('   * unsaved changes *' if dirty else '')
            ]
            all_pages = pages(cfg['look'])
            title, settings = all_pages[page % len(all_pages)]
            lines.append(f'[{title}]  page {page % len(all_pages) + 1}/{len(all_pages)} (Tab)')
            for i, setting in enumerate(settings):
                mark = '>' if i == selected else ' '
                lines.append(
                    f'{mark} {(i + 1) % 10}  {setting[0]:<24s} {get_value(cfg["look"], setting):g}'
                )
            lines += [
                '1-9, 0 select  up/down adjust (Shift = x5)  Tab page  left/right stage',
                'T reset  S save  R reload config  H help  Q quit',
            ]
            if message:
                lines.append(message)
            ui.draw_help(screen, font, lines, top=top + 20)
        pygame.display.flip()

    rerender()
    show()
    running = True
    while running:
        # the desk page redraws every 16 ms (the levels move), the others
        # sleep on the event queue
        event = pygame.event.wait(16) if on_desk_page() else pygame.event.wait()
        if event.type == pygame.QUIT:
            running = False
        elif panel.visible and panel.handle(event):
            pass
        elif event.type == pygame.KEYDOWN:
            shift = event.mod & pygame.KMOD_SHIFT
            steps = 5 if shift else 1
            message = ''
            if event.key in ui.QUIT_KEYS:
                running = False
            elif event.key in SELECT_KEYS:
                selected = SELECT_KEYS[event.key]
                if on_desk_page():
                    panel.select(selected)
            elif event.key == pygame.K_TAB:
                set_page((page + 1) % len(pages(cfg['look'])))
            elif event.key in (pygame.K_UP, pygame.K_DOWN) and on_desk_page():
                panel.nudge(steps if event.key == pygame.K_UP else -steps)
            elif event.key in (pygame.K_UP, pygame.K_DOWN):
                all_pages = pages(cfg['look'])
                settings = all_pages[page % len(all_pages)][1]
                if selected < len(settings):
                    adjust(
                        cfg['look'],
                        settings[selected],
                        steps if event.key == pygame.K_UP else -steps,
                    )
                    dirty = True
                    rerender()
            elif event.key == pygame.K_RIGHT:
                idx = min(idx + 1, len(stages) - 1)
                rerender()
            elif event.key == pygame.K_LEFT:
                idx = max(idx - 1, 0)
                rerender()
            elif event.key in (pygame.K_t, pygame.K_s) and on_desk_page():
                message = 'the desk page saves nothing (Tab to the look pages for S)'
            elif event.key == pygame.K_t:
                cfg['look'] = copy.deepcopy(snap)
                dirty = True
                message = 'settings reset to startup values'
                rerender()
            elif event.key == pygame.K_s:
                calibration.save_config(cfg, config)
                dirty = False
                message = f'saved to {config}'
            elif event.key == pygame.K_r:
                cfg = render.load_config(config)
                cfg['look'] = render.look_settings(cfg)
                snap = copy.deepcopy(cfg['look'])
                stages = load_views(scenes_path, cfg)
                idx = min(idx, len(stages) - 1)
                renderer = build_renderer()
                lights = lamps.Lamps(cfg)
                dirty = False
                message = f'reloaded {config}'
                rerender()
            elif event.key == pygame.K_h:
                show_help = not show_help
            if not on_desk_page():
                # drop key presses queued while rendering: one step per
                # press (on the desk page they are the repeats that slide)
                pygame.event.clear(pygame.KEYDOWN)
        elif event.type in (pygame.VIDEOEXPOSE, pygame.WINDOWEXPOSED):
            pass
        show()

    desk.close()
    pygame.key.set_repeat()
    pygame.mouse.set_visible(False)
    if standalone:
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description='Tune brightness and the spotlights')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--scenes', default='scenes.yaml')
    ap.add_argument('--supersample', type=int, default=3)
    ap.add_argument(
        '--test', action='store_true', help='self-test: render one stage with a spot, no display'
    )
    ap.add_argument(
        '--dmx',
        choices=dmx.SOURCES,
        help='override dmx.source for the desk page (demo = a scripted desk)',
    )
    args = ap.parse_args()
    if args.test:
        self_test(render.load_config(args.config), args.scenes)
        return
    run(
        config=args.config,
        scenes_path=args.scenes,
        supersample=args.supersample,
        dmx_source=args.dmx,
    )


if __name__ == '__main__':
    main()
