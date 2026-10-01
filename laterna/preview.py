#!/usr/bin/env python
"""Preview: the show scene by scene, step 6, an interactive cue sheet.

Every scene as a still, lit at full as Look shows it (laterna/look.py):
no fades, a slideshow on the picture it ends up on, a text that comes in
line by line whole, a blackout black. Over it all there is to know about
the scene: its number and name, its description, how it is reached and
how the show goes on from it (a key, the desk, by itself after the hold),
the fades, hold and transition_time, and per frame what it shows (a
picture, a slideshow with its timing and loop, a video, a text with its
lines and pace, nothing) with its options (spot, fit, size) and the
scene's colours.

Keys:
  right / space / Enter   next scene
  left / Backspace        previous scene
  Home / End              first / last scene
  H                       the information on/off
  Q / ESC                 quit (back to the menu)
"""

import argparse

import pygame

from . import dmx, lamps, look, render, ui


def _seconds(t):
    return f'{t:g} s'


def _base(path):
    return str(path).replace('\\', '/').split('/')[-1]


def _blackout(scene):
    return bool(scene.get('blackout'))


def entry_line(i, scenes, fades):
    """How scene i is reached: the start, a key (or the desk), the previous
    scene's hold; and the fade into it."""
    if i == 0:
        return 'in: the start of the show'
    prev = scenes[i - 1]
    fade = f'fade in {_seconds(fades[i - 1])}'
    if prev.get('hold') is not None:
        return f'in: by itself, {_seconds(prev["hold"])} after scene {i} stands   {fade}'
    if _blackout(scenes[i]):
        return f'in: a key, or the desk: the master to 0   {fade}'
    if _blackout(prev):
        return f'in: a key, or the desk: the master up   {fade}'
    return f'in: a key   {fade}'


def next_line(i, scenes, fades):
    """How the show goes on from scene i, and the fade to the next."""
    if i + 1 >= len(scenes):
        return 'on: the last scene'
    scene, fade = scenes[i], f'fade out {_seconds(fades[i])}'
    if scene.get('hold') is not None:
        return f'on: by itself after the hold of {_seconds(scene["hold"])}   {fade}'
    if _blackout(scene):
        return f'on: a key, or the desk: the master up   {fade}'
    if _blackout(scenes[i + 1]):
        return f'on: a key, or the desk: the master to 0   {fade}'
    return f'on: a key   {fade}'


def mapping_line(m):
    """What a mapping shows, in a line."""
    options = []
    if 'text' in m:
        lines = sum(1 for t in m['text'] if _whole(t).strip())
        what = f'text, {lines} line{"s" * (lines != 1)}'
        if 'slideshow' in m:
            step = float(m.get('hold', 0)) + float(m.get('transition_time', 0))
            steps = len(m['slideshow']) - 1
            what += (
                f', line by line: a step every {_seconds(step)}, whole at {_seconds(steps * step)}'
            )
        first = next((_whole(t) for t in m['text'] if _whole(t).strip()), '')
        what += f'  "{first[:40]}{"..." if len(first) > 40 else ""}"'
    elif 'slideshow' in m:
        hold = m.get('hold', render.SLIDESHOW_DEFAULTS['hold'])
        fade = m.get('transition_time', render.SLIDESHOW_DEFAULTS['transition_time'])
        loop = '' if m.get('loop', True) else ', no loop'
        files = ', '.join(_base(f) for f in m['slideshow'])
        what = f'slideshow of {len(m["slideshow"])} (hold {_seconds(hold)}, fade {_seconds(fade)}{loop}): {files}'
    elif 'video' in m:
        what = f'video {_base(m["video"])}'
    else:
        what = _base(m.get('image', '?'))
    if m.get('spot') is False:
        options.append('no spot')
    for key in ('fit', 'width', 'height'):
        if m.get(key) is not None:
            options.append(f'{key} {m[key]}')
    return what + (f'   [{", ".join(options)}]' if options else '')


def _whole(t):
    """A text line as one string (a string, a list of parts, or {text: ...})."""
    t = t['text'] if isinstance(t, dict) else t
    return ''.join(t) if isinstance(t, list) else t


def scene_lines(i, scenes, fades, cfg):
    """All there is to tell about scene i, as lines for the overlay."""
    scene = scenes[i]
    lines = [f'{i + 1} / {len(scenes)}   {scene.get("name", "?")}']
    if scene.get('description'):
        lines += _wrap(scene['description'], 90)
    lines.append('')
    lines.append(entry_line(i, scenes, fades))
    lines.append(next_line(i, scenes, fades))
    hold = scene.get('hold')
    lines.append(
        f'hold: {"none (waits)" if hold is None else _seconds(hold)}   '
        f'transition_time: {_seconds(scene["transition_time"])}'
    )
    lines.append('')
    if _blackout(scene):
        lines.append('blackout: everything black')
        return lines
    names = {o['id']: o.get('name', o['id']) for o in cfg['objects']}
    on = {oid: m for m in scene['mappings'] for oid in m['objects']}
    seen = set()
    for oid, name in names.items():
        m = on.get(oid)
        if m is None:
            lines.append(f'{name}: empty (fill colour)')
        elif id(m) in seen:
            lines.append(f'{name}: the same as above')
        else:
            seen.add(id(m))
            more = f' (with {len(m["objects"]) - 1} more)' if len(m['objects']) > 1 else ''
            lines.append(f'{name}{more}: {mapping_line(m)}')
    for key in ('molding_color', 'fill_color'):
        if scene.get(key) is not None:
            lines.append(f'{key}: {list(scene[key])}')
    return lines


def _wrap(text, width):
    out, line = [], ''
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f'{line} {word}'.strip()
    return out + ([line] if line else [])


def run(screen=None, config='config.yaml', scenes_path='scenes.yaml', supersample=2):
    """Run the app; with a screen provided, reuse it (menu mode)."""
    cfg = render.load_config(config)
    cfg['look'] = render.look_settings(cfg)
    scenes, fades = render.parse_scenes(render.load_scenes(scenes_path), cfg)
    if not scenes:
        raise ValueError('no scenes in scenes.yaml')
    ids = [o['id'] for o in cfg['objects']]
    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Preview — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Preview — {ui.APP_NAME}')
    font = ui.help_font()
    pygame.key.set_repeat()
    lights = lamps.Lamps(cfg)
    stills = {}  # scene index -> (surface, molding), rendered when first shown
    state = {'renderer': None}

    def still(i):
        if i not in stills:
            screen.fill((0, 0, 0))
            ui.draw_help(screen, font, [f'rendering scene {i + 1}...'])
            pygame.display.flip()
            if state['renderer'] is None:
                state['renderer'] = render.SceneRenderer(cfg, ss=supersample)
            scene = {**scenes[i], 'mappings': [look._still(m) for m in scenes[i]['mappings']]}
            r = state['renderer']
            stills[i] = (r.render(scene), r.render_molding(scene))
        return stills[i]

    def show(i, info):
        if _blackout(scenes[i]):
            screen.fill((0, 0, 0))
        else:
            surface, molding = still(i)
            levels = dmx.Levels.full(float(cfg['look']['temperature']), ids)
            lights.light(screen, surface, molding, levels)
        if info:
            lines = scene_lines(i, scenes, fades, cfg)
            lines += ['', 'left/right scene  Home/End first/last  H info  Q quit']
            ui.draw_help(screen, font, lines)
        pygame.display.flip()

    idx, info = 0, True
    show(idx, info)
    running = True
    while running:
        event = pygame.event.wait()
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            step = None
            if event.key in ui.QUIT_KEYS:
                running = False
                continue
            if event.key in (pygame.K_RIGHT, pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
                step = idx + 1
            elif event.key in (pygame.K_LEFT, pygame.K_BACKSPACE):
                step = idx - 1
            elif event.key == pygame.K_HOME:
                step = 0
            elif event.key == pygame.K_END:
                step = len(scenes) - 1
            elif event.key == pygame.K_h:
                info = not info
            if step is not None:
                idx = min(max(step, 0), len(scenes) - 1)
            show(idx, info)
            pygame.event.clear(pygame.KEYDOWN)  # one step per press (rendering takes a moment)
        elif event.type in (pygame.VIDEOEXPOSE, pygame.WINDOWEXPOSED):
            show(idx, info)
    pygame.mouse.set_visible(False)
    if standalone:
        pygame.quit()


def self_test(config, scenes_path):
    """Headless check: every scene's lines, no display."""
    cfg = render.load_config(config)
    scenes, fades = render.parse_scenes(render.load_scenes(scenes_path), cfg)
    for i in range(len(scenes)):
        for line in scene_lines(i, scenes, fades, cfg):
            print(line)
        print('-' * 60)
    print('self-test ok')


def main():
    ap = argparse.ArgumentParser(description='The show scene by scene, with all there is to know')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--scenes', default='scenes.yaml')
    ap.add_argument('--supersample', type=int, default=2)
    ap.add_argument('--test', action='store_true', help="self-test: print every scene's lines")
    args = ap.parse_args()
    if args.test:
        self_test(args.config, args.scenes)
        return
    run(config=args.config, scenes_path=args.scenes, supersample=args.supersample)


if __name__ == '__main__':
    main()
