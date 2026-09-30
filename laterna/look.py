#!/usr/bin/env python
"""Look: brightness per layer and the pretend spotlights (`look` in config.yaml).

Shows the scenes from scenes.yaml as the presentation renders them (no
fades; video polygons stay black here) and lets you tune, live. The
first view has every canvas white (the light itself, on a blank
canvas). Blackout scenes are skipped (nothing to look at) and a scene
with slideshows is shown once per slide: view k has every slideshow of
the scene on its k-th image (shorter ones wrap), so each image gets
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

Everything shows at full light, without the desk: the desk and its
faders are the DMX app's (laterna/desk.py).

Keys:
  1..9, 0        select a setting on the current page (see the overlay)
  Tab            next page (look / picture spot / molding spot)
  up / down      adjust the selected setting  (with Shift: x5)
  left / right   previous / next scene
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

from . import calibration, dmx, lamps, render, ui

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
        ('frame depth (mm, 0 = off)', ('frame_depth',), 1.0, 0.0, 50.0),
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


def pages(look):
    """The settings pages for a (completed) look dict."""
    out = [PAGE_LOOK, _spot_page('picture spot', 'spot', look['spot'])]
    if look.get('molding_spot'):
        out.append(_spot_page('molding spot', 'molding_spot', look['molding_spot']))
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


def views(scenes):
    """The scenes as the look app shows them: first every canvas white
    (the light on a blank canvas), then the scenes with blackouts left
    out and a scene with slideshows expanded to one view per slide (the
    k-th image of every slideshow in the scene; video items keep their
    `video:` and stay black, like video mappings). Each view is a scene
    dict render() accepts."""
    out = [WHITE_VIEW]
    for scene in scenes:
        if scene.get('blackout'):
            continue
        shows = [m for m in scene.get('mappings', []) if 'slideshow' in m]
        if not shows:
            out.append(scene)
            continue
        count = max(len(m['slideshow']) for m in shows)
        for k in range(count):
            mappings = []
            for m in scene['mappings']:
                if 'slideshow' in m:
                    n = len(m['slideshow'])
                    item = m['slideshow'][k % n if m.get('loop', True) else min(k, n - 1)]
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
                    **scene,
                    'name': f'{scene.get("name", "?")} [{k + 1}/{count}]',
                    'mappings': mappings,
                }
            )
    return out


def load_views(scenes_path, cfg):
    scenes, _ = render.parse_scenes(render.load_scenes(scenes_path), cfg)
    return views(scenes)


def self_test(cfg, scenes_path):
    """Headless check: one render plus the edit functions; does not save."""
    cfg['look'] = render.look_settings(cfg)
    scenes = load_views(scenes_path, cfg)
    look_page, spot_page = pages(cfg['look'])[:2]
    adjust(cfg['look'], spot_page[1][0], 7)  # spot strength +0.35
    adjust(cfg['look'], look_page[1][0], -2)  # molding brightness -0.1
    renderer = render.SceneRenderer(cfg, ss=2)
    # what the lamps do at full light: the headroom back, in the light's colour
    gain = render.headroom_gain(cfg) * render.white_gain(
        cfg['look']['temperature'], cfg['look']['white']
    )

    def lit(image):
        return np.clip(image.astype(np.float32) * gain, 0, 255).astype(np.uint8)

    render.save_png(lit(renderer.render(scenes[min(3, len(scenes) - 1)])), '_renders/look.png')
    render.save_png(lit(renderer.render(scenes[0])), '_renders/look-white.png')
    print('self-test ok (written: _renders/look.png, _renders/look-white.png)')


def run(screen=None, config='config.yaml', scenes_path='scenes.yaml', supersample=3):
    """Run the app; with a screen provided, reuse it (menu mode)."""
    cfg = render.load_config(config)
    cfg['look'] = render.look_settings(cfg)  # complete, so every setting exists
    snap = copy.deepcopy(cfg['look'])
    scenes = load_views(scenes_path, cfg)
    ids = [o['id'] for o in cfg['objects']]

    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Look — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Look — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    font = ui.help_font()
    pygame.key.set_repeat()  # every step re-renders (~1 s): no key repeat
    lights = lamps.Lamps(cfg)

    def build_renderer():
        screen.fill((0, 0, 0))
        ui.draw_help(screen, font, ['preparing the molding...'])
        pygame.display.flip()
        return render.SceneRenderer(cfg, ss=supersample)

    renderer = build_renderer()

    page, selected = 0, 0
    idx = 0  # the white canvases first
    dirty = False
    show_help = True
    message = ''
    surface = molding = None

    def rerender():
        nonlocal surface, molding
        renderer.apply_look()
        lights.white = float(cfg['look']['white'])
        lights.collapse = float(cfg['look']['spot_collapse'])
        surface = renderer.render(scenes[idx])
        molding = renderer.render_molding(scenes[idx])

    def show():
        # full light at the look's colour temperature
        levels = dmx.Levels.full(float(cfg['look']['temperature']), ids)
        lights.light(screen, surface, molding, levels)
        if show_help:
            lines = [
                f'view {idx + 1}/{len(scenes)}: {scenes[idx].get("name", "?")}'
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
                '1-9, 0 select  up/down adjust (Shift = x5)  Tab page  left/right scene',
                'T reset  S save  R reload config  H help  Q quit',
            ]
            if message:
                lines.append(message)
            ui.draw_help(screen, font, lines)
        pygame.display.flip()

    rerender()
    show()
    running = True
    while running:
        event = pygame.event.wait()
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            shift = event.mod & pygame.KMOD_SHIFT
            steps = 5 if shift else 1
            message = ''
            if event.key in ui.QUIT_KEYS:
                running = False
            elif event.key in SELECT_KEYS:
                selected = SELECT_KEYS[event.key]
            elif event.key == pygame.K_TAB:
                page, selected = (page + 1) % len(pages(cfg['look'])), 0
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
                idx = min(idx + 1, len(scenes) - 1)
                rerender()
            elif event.key == pygame.K_LEFT:
                idx = max(idx - 1, 0)
                rerender()
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
                scenes = load_views(scenes_path, cfg)
                idx = min(idx, len(scenes) - 1)
                renderer = build_renderer()
                lights = lamps.Lamps(cfg)
                dirty = False
                message = f'reloaded {config}'
                rerender()
            elif event.key == pygame.K_h:
                show_help = not show_help
            # drop key presses queued while rendering: one step per press
            pygame.event.clear(pygame.KEYDOWN)
        elif event.type in (pygame.VIDEOEXPOSE, pygame.WINDOWEXPOSED):
            pass
        show()

    pygame.mouse.set_visible(False)
    if standalone:
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description='Tune brightness and the spotlights')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--scenes', default='scenes.yaml')
    ap.add_argument('--supersample', type=int, default=3)
    ap.add_argument(
        '--test', action='store_true', help='self-test: render one scene with a spot, no display'
    )
    args = ap.parse_args()
    if args.test:
        self_test(render.load_config(args.config), args.scenes)
        return
    run(
        config=args.config,
        scenes_path=args.scenes,
        supersample=args.supersample,
    )


if __name__ == '__main__':
    main()
