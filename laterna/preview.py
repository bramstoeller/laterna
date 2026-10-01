#!/usr/bin/env python
"""Preview: the show scene by scene, step 6, an interactive cue sheet.

Every lit scene as a still, lit at full as Look shows it
(laterna/look.py): no fades, a slideshow on the picture it ends up on, a
text that comes in line by line whole; the blackouts are left out (the
numbers stay the show's, so a gap shows where one is). The scenes render
in the background when Preview starts, each once, and are kept in
_cache/preview/ under a key of everything they depend on (Present's
render key and the stills' scenes), so the next start loads them. Over it all there is to know about
the scene: its number and name, its description, how it is reached and
how the show goes on from it (a key, the desk, by itself after the hold),
the fades, hold and transition_time, and per frame what it shows (a
picture, a slideshow with its timing and loop, a video, a text with its
lines and pace, nothing) with its options (spot, fit, size) and the
scene's colours.

Keys:
  right / space / Enter   next scene (a blackout is skipped)
  left / Backspace        previous scene
  Home / End              first / last scene
  H                       the information on/off
  Q / ESC                 quit (back to the menu)
"""

import argparse
import hashlib
import pathlib
import threading

import cv2
import numpy as np
import pygame

from . import dmx, lamps, look, render, ui

CACHE = pathlib.Path('_cache') / 'preview'  # in the show folder; Present keeps its own beside it


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
    """Run the app; with a screen provided, reuse it (menu mode). The lit
    scenes (blackouts are left out) render in the background from the
    first on, each once; stepping to one not done yet waits for it."""
    cfg = render.load_config(config)
    cfg['look'] = render.look_settings(cfg)
    scenes, fades = render.parse_scenes(render.load_scenes(scenes_path), cfg)
    lit = [i for i, scene in enumerate(scenes) if not _blackout(scene)]
    if not lit:
        raise ValueError('no scenes to show in scenes.yaml (only blackouts)')
    ids = [o['id'] for o in cfg['objects']]
    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Preview — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Preview — {ui.APP_NAME}')
    font = ui.help_font()
    pygame.key.set_repeat()
    lights = lamps.Lamps(cfg)
    stills = {}  # scene index -> (surface, molding), filled by the worker
    stop = threading.Event()
    shown = {
        i: {**scenes[i], 'mappings': [look._still(m) for m in scenes[i]['mappings']]} for i in lit
    }
    folder = pathlib.Path(cfg['_dir']) / CACHE
    key = _key(config, scenes_path, cfg, scenes, shown, supersample)

    def worker():
        if _read(folder / 'key.txt') != key:  # another show state: start over
            if folder.exists():
                for old in folder.glob('*.png'):
                    old.unlink(missing_ok=True)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'key.txt').write_text(key)
        renderer = None
        for i in lit:
            if stop.is_set():
                return
            files = folder / f'scene-{i:02d}.png', folder / f'molding-{i:02d}.png'
            pair = [_load(f) for f in files]
            if any(p is None for p in pair):
                renderer = renderer or render.SceneRenderer(cfg, ss=supersample)
                pair = [renderer.render(shown[i]), renderer.render_molding(shown[i])]
                for f, image in zip(files, pair):
                    _save(f, image)
            stills[i] = tuple(pair)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def show(i, info):
        surface, molding = stills[i]
        levels = dmx.Levels.full(float(cfg['look']['temperature']), ids)
        lights.light(screen, surface, molding, levels)
        if info:
            lines = scene_lines(i, scenes, fades, cfg)
            lines += ['', 'left/right scene  Home/End first/last  H info  Q quit']
            ui.draw_help(screen, font, lines)
        pygame.display.flip()

    def wait_for(i):
        """Until scene i is rendered; False when quit meanwhile."""
        while i not in stills:
            screen.fill((0, 0, 0))
            ready = sum(1 for k in lit if k in stills)
            ui.draw_help(screen, font, [f'rendering the scenes... {ready} of {len(lit)}'])
            pygame.display.flip()
            event = pygame.event.wait(200)
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key in ui.QUIT_KEYS
            ):
                return False
            if not thread.is_alive() and i not in stills:
                raise RuntimeError(f'scene {i + 1} could not be rendered')
        return True

    pos, info = 0, True  # pos: the place in `lit`
    running = wait_for(lit[pos])
    if running:
        show(lit[pos], info)
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
                step = pos + 1
            elif event.key in (pygame.K_LEFT, pygame.K_BACKSPACE):
                step = pos - 1
            elif event.key == pygame.K_HOME:
                step = 0
            elif event.key == pygame.K_END:
                step = len(lit) - 1
            elif event.key == pygame.K_h:
                info = not info
            if step is not None:
                pos = min(max(step, 0), len(lit) - 1)
            if not wait_for(lit[pos]):
                break
            show(lit[pos], info)
        elif event.type in (pygame.VIDEOEXPOSE, pygame.WINDOWEXPOSED):
            show(lit[pos], info)
    stop.set()
    pygame.mouse.set_visible(False)
    if standalone:
        pygame.quit()


def _key(config, scenes_path, cfg, scenes, shown, supersample):
    """What the stills depend on: Present's render key (config, scenes,
    media, the rendering code) and the scenes as Preview shows them."""
    from . import play

    h = hashlib.sha256(play._fingerprint(config, scenes_path, cfg, scenes, supersample).encode())
    h.update(repr(sorted(shown.items())).encode())
    return h.hexdigest()


def _read(path):
    try:
        return pathlib.Path(path).read_text()
    except OSError:
        return None


def _save(path, rgb):
    """A still as a PNG, by bytes (any path, also on Windows), whole or not
    at all (written next to it, then renamed)."""
    ok, png = cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if ok:
        part = path.with_name(path.name + '.part')
        part.write_bytes(png.tobytes())
        part.replace(path)


def _load(path):
    """A still saved by _save (RGB), or None."""
    try:
        data = np.frombuffer(pathlib.Path(path).read_bytes(), np.uint8)
    except OSError:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return None if image is None else cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


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
