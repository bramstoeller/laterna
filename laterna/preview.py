#!/usr/bin/env python
"""Preview: the show scene by scene, step 6, an interactive cue sheet.

Every lit scene as a still, lit at full as Look shows it
(laterna/look.py): no fades, a slideshow on the picture it ends up on, a
text that comes in line by line whole; the blackouts are left out (the
numbers stay the show's, so a gap shows where one is). The stills come
from Present's render cache (_cache/, laterna/play.py): when it is valid
they load at once, else Preview renders the whole show into it in the
background, the lit scenes as they come, so Present starts without a
render afterwards. Over it all there is to know about
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
import threading

import cv2
import pygame
import yaml

from . import dmx, lamps, play, render, ui, video


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


def still(cfg, scene, base, alpha, lut):
    """The scene as Preview shows it from its render: a slideshow (a text
    that comes in line by line is one) on the picture it ends up on (the
    last without loop, else the first), a video on its first frame."""
    if alpha is None:
        return base
    image = base.copy()
    flat = image.reshape(-1, 3)
    for spec in video.build_specs(cfg, scene, base, alpha):
        if spec['kind'] == 'slideshow':
            count, loop = spec['timing'][0], spec['timing'][3]
            rows, _ = video._slide_rows(spec, 0 if loop else count - 1, 0.0, lut)
        else:
            clip = video.VideoClip(spec['path'])
            frame = clip.frame_at(0.0)
            if clip.ok:
                clip.cap.release()
            if frame is None:
                continue
            rows = video._compose_rows(video._fit_region(frame, spec), spec, lut)
        flat[spec['flat']] = rows
    return image


def run(screen=None, config='config.yaml', scenes_path='scenes.yaml', supersample=3):
    """Run the app; with a screen provided, reuse it (menu mode). The
    stills come from Present's render cache, made here when it is not
    valid (see the module doc); stepping to one not ready yet waits."""
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
    lut = render.gamma_lut(cfg)
    stills = {}  # scene index -> (surface, molding), filled by the worker
    stop = threading.Event()
    play._stop_worker()  # a Present render still going: never two writing _cache/
    fp = play._fingerprint(config, scenes_path, cfg, scenes, supersample)

    def from_cache():
        moldings = {}
        for i in lit:
            if stop.is_set():
                return
            key = play._molding_key(scenes[i])
            if key not in moldings:
                moldings[key] = _rgb(play._molding_file(key))
            alpha = None
            if play._has_video(scenes[i]):
                alpha = cv2.imread(str(play._alpha_file(i)), cv2.IMREAD_GRAYSCALE)
            base = _rgb(play._scene_file(i))
            stills[i] = (still(cfg, scenes[i], base, alpha, lut), moldings[key])

    def make_cache():
        """Every scene rendered into Present's cache (its order and files),
        the manifest last: only a complete render makes it valid."""
        play._clear_cache()
        play.CACHE_DIR.mkdir(exist_ok=True)
        renderer = render.SceneRenderer(cfg, ss=supersample)
        moldings = {}
        for i, scene in enumerate(scenes):
            if stop.is_set():
                return
            base = renderer.render(scene)
            render.save_png(base, play._scene_file(i))
            alpha = renderer.video_alpha(scene)
            if alpha is not None:
                cv2.imwrite(str(play._alpha_file(i)), alpha)
            key = play._molding_key(scene)
            if key != 'blackout' and key not in moldings:
                moldings[key] = renderer.render_molding(scene)
                render.save_png(moldings[key], play._molding_file(key))
            if not _blackout(scene):
                stills[i] = (still(cfg, scene, base, alpha, lut), moldings[key])
        if not stop.is_set():
            play.MANIFEST.write_text(yaml.safe_dump({'fingerprint': fp, 'scenes': len(scenes)}))

    valid = play._manifest_valid(fp, scenes)
    thread = threading.Thread(target=from_cache if valid else make_cache, daemon=True)
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
        """Until scene i is ready; False when quit meanwhile."""
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

    try:
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
                if event.key in (
                    pygame.K_RIGHT,
                    pygame.K_SPACE,
                    pygame.K_RETURN,
                    pygame.K_KP_ENTER,
                ):
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
    finally:
        # the render stops after the scene it is on: no manifest then, so
        # Present renders anew; never two threads writing _cache/
        stop.set()
        thread.join()
    pygame.mouse.set_visible(False)
    if standalone:
        pygame.quit()


def _rgb(path):
    data = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if data is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(data, cv2.COLOR_BGR2RGB)


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
    ap.add_argument('--supersample', type=int, default=3)
    ap.add_argument('--test', action='store_true', help="self-test: print every scene's lines")
    args = ap.parse_args()
    if args.test:
        self_test(args.config, args.scenes)
        return
    run(config=args.config, scenes_path=args.scenes, supersample=args.supersample)


if __name__ == '__main__':
    main()
