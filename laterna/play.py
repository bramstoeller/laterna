#!/usr/bin/env python
"""Play the stages from scenes.yaml on the projector.

Keys:
  Enter / Space / right arrow   next stage
  Backspace / left arrow   previous stage
  (during a fade a step key does nothing; the same key again within half
   a second cuts the fade short and steps on)
  < and >  (also , and .)   a picture back / on inside a slideshow
  H  stage info on/off (small label, off by default; with a desk also
     its channels)
  Tab  the desk's channels as faders across the top (hidden feature):
     they follow the desk, and can be dragged (laterna/faders.py); Tab or
     Esc closes them again
  Q / ESC  quit (back to the menu when started from main.py)

A small bar in the top right corner tells the operator what the show is
doing: red while a transition runs, orange while a stage's hold runs,
green when it waits for a key, dim blue in a blackout with the desk's
master at 0 (the master coming up goes on, see below). It is a row of
2 x 2 px blocks 2 px apart: one in the corner plus, darker, one for every
whole second left (rounded up), so red and orange lose a block per
second towards the corner; green and blue are just the corner block.
A countdown too long to fit in a quarter of the width goes to 2, 3, 5,
10, 15, 20, 30 seconds a block and then whole minutes; the step follows
the whole stretch being counted, so it never changes halfway and the bar
shrinks smoothly. The left and the
right bars work out their own step, and all the left ones share theirs,
so those stay comparable with each other.

Inside a slideshow, `>` steps to the next picture (starting the fade the
show would run there by itself) and `<` cuts back to the previous one;
the stage keys (Enter, space, the arrows) leave the slideshow and move
between stages as always.

The slideshows and videos inside a stage get their own bars in the top
LEFT corner, one row per distinct timing in the order the stage maps
them, growing to the right and in dark grey so they stay out of the way:
counting down hold + fade to the next picture fully in, or to the moment
a video starts over. Shows that run in step (same timing, same moment)
share one row; two shows of different lengths get two rows, each on its
own clock.

A stage with `hold: <seconds>` (its own or the global default at the top
of scenes.yaml) moves on to the next stage by itself that long after its
fade-in finished; a key press still works earlier. Without a hold the
stage waits for a key. Its `transition_time` (or the global one) is the
fade to the next stage.

With a light desk the master also steps in and out of blackout stages.
A stage waiting for a key, followed by a blackout stage, goes into it
when the desk's master goes to 0 after the stage has been lit; a
blackout stage waiting for a key goes on to the next stage when the
master comes up after having been 0 there. Both at once, without the
fade: the desk does the fading. So the desk can take the show through
its dark moments by itself, and a key still works as always.

Transitions are crossfades. The duration comes from `- fade: <seconds>`
entries between stages in scenes.yaml; where absent, the global `fade`
applies (default 1.0 s). All calibration (global and per object) comes from
config.yaml, as saved by align_global.py and configure.py.

The first stage is shown as soon as it is ready; the remaining stages render
in a background thread (the border molding is computed only once). A small
progress line shows until everything is rendered; stepping to a stage that
is not ready yet waits for it.

A mapping with `video:` instead of `image:` plays a looping clip inside its
polygons (see laterna/video.py): the cached render is then the static part with
the video polygons black, plus an alpha layer, and the due frame is
composited in per tick. Clips follow the wall clock — when decoding falls
behind, frames are dropped, never slowed down. A clip restarts from the top
when its stage is entered, except when the previous stage was already
playing the same file: then it runs on seamlessly.

A stage with `blackout: true` is all black. The fade into it (and out
of it) takes the usual fade time and dims everything together, as if
the desk's master fader went down: the light reddens like a tungsten
spot's on the way (laterna/lamps.py); no rendering.

The light desk can dim it all: `dmx:` in config.yaml (laterna/dmx.py) gives
the desk a master, the light's colour temperature and per object a lamp
on its canvas and one on its frame, over sACN, Art-Net or an Enttec
widget. The levels multiply into everything above, fades
included; the lamps (laterna/lamps.py) apply them per tick. H also shows
the desk's channels.

A mapping with `slideshow: [images]` shows those images in turn inside its
polygons: each holds `hold` seconds, then a `transition` (only `fade` so
far) of `transition_time` seconds leads to the next, wrapping after the
last. It is played like a video (alpha layer, per-tick compositing) and
follows the same rules for starting, restarting and running on.
"""

import argparse
import hashlib
import math
import pathlib
import sys
import threading

import cv2
import numpy as np
import pygame
import yaml

from . import dmx, faders, lamps, render, ui, video

# the state bar in the top right corner (see the module doc): 2 x 2 px
# blocks 2 px apart, one plus one per whole second left
STATE_PX = 2  # block size
STATE_PITCH = 4  # block + gap
# (the block in the corner, the countdown behind it), dark enough to read
# from the desk without the audience noticing
STATE_FADE = ((128, 0, 0), (64, 0, 0))  # a transition runs
STATE_HOLD = ((128, 64, 0), (64, 32, 0))  # a stage hold runs
STATE_KEY = ((0, 128, 0), (0, 64, 0))  # waiting for a key
STATE_DESK = ((0, 0, 128), (0, 0, 128))  # a blackout waits for the master to come up
STATE_CLIP = ((64, 64, 64), (64, 64, 64))  # the clips' own bars, left, flat grey
BAR_TICK_MS = 250  # redraw interval during a hold (1 px of bar)
BAR_LADDER = (1, 2, 3, 5, 10, 15, 20, 30)  # seconds per block, then whole minutes
# the keys that step between stages, and how long a second press of the same
# one may take to count as "cut this fade short and step on" (see pump)
STEP_KEYS = {
    pygame.K_RETURN: 1,
    pygame.K_KP_ENTER: 1,
    pygame.K_SPACE: 1,
    pygame.K_RIGHT: 1,
    pygame.K_BACKSPACE: -1,
    pygame.K_LEFT: -1,
}
DOUBLE_PRESS = 0.5
DARK = 0.5 / 255.0  # the master below this is 0 (half a DMX value)


def bar_unit(span, max_blocks):
    """Seconds per block for a countdown of `span` seconds: the finest step
    of BAR_LADDER that keeps the bar inside `max_blocks`, then whole
    minutes."""
    for unit in BAR_LADDER:
        if 1 + math.ceil(span / unit) <= max_blocks:
            return unit
    return 60 * max(1, math.ceil(span / 60.0 / max(max_blocks - 1, 1)))


# rendered stages on disk, so a restart skips rendering
CACHE_DIR = pathlib.Path('_cache')
MANIFEST = CACHE_DIR / 'manifest.yaml'

# background render of the previous run in this process, if it is still
# going: (thread, stop event). Quitting to the menu while stages were still
# rendering used to leave that thread writing cache files — and finally the
# manifest — underneath the next run, mixing old and new renders.
_worker = None


def _stop_worker():
    """Cancel and join a render thread left by an earlier run, so two runs
    never write _cache/ at the same time."""
    global _worker
    if _worker is not None:
        thread, stop = _worker
        stop.set()
        thread.join()
        _worker = None


def _stage_file(i):
    return CACHE_DIR / f'stage-{i:02d}.png'


def _alpha_file(i):
    return CACHE_DIR / f'stage-{i:02d}-alpha.png'


def _is_blackout(stage):
    return bool(stage.get('blackout'))


def _molding_key(stage):
    """What a stage's molding render depends on: its molding_color (None =
    the default gold), or 'blackout' (all black, never cached)."""
    return 'blackout' if _is_blackout(stage) else stage.get('molding_color')


def _molding_file(key):
    name = 'default' if key is None else '-'.join(str(c) for c in key)
    return CACHE_DIR / f'molding-{name}.png'


def _molding_keys(stages):
    """The distinct molding renders the stages need, in order of first use."""
    return list(dict.fromkeys(_molding_key(s) for s in stages if not _is_blackout(s)))


def _has_video(stage):
    """True when the stage needs per-tick compositing (video or slideshow)."""
    return any(render.is_animated(m) for m in stage.get('mappings', []))


def dim(screen, lights, get_full, molding, out, duration, clock, levels, overlay, pump):
    """Fade the light between a live stage (`get_full`, re-evaluated per tick
    so videos keep playing) and black, everything together like a master
    fader (`out` = into the blackout, else out of it), on top of what the
    desk asks (`levels`, called per tick), through the lamps (which redden
    it on the way); `overlay` draws what goes on top before each flip,
    `pump` handles the events that may act during a fade."""

    def frame(t):
        level = 1.0 - t if out else t
        lights.light(screen, get_full(), molding, levels().scaled(level, level))
        overlay()
        pygame.display.flip()

    if duration > 0:
        start = pygame.time.get_ticks()
        while True:
            t = (pygame.time.get_ticks() - start) / 1000.0 / duration
            if t >= 1.0 or pump():  # pump: True = cut this fade short
                break
            frame(t)
            clock.tick(60)
    frame(1.0)


def _hash_file(h, path):
    """Feed a file into the hash in chunks (videos can be large)."""
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
    except OSError:
        h.update(b'missing')


def _fingerprint(config, scenes_path, cfg, stages, supersample):
    """Hash of everything the rendered stages depend on: the two YAML files,
    the supersample factor, the bytes of every shape SVG and every
    referenced image/video, and the rendering code itself."""
    h = hashlib.sha256()
    h.update(pathlib.Path(config).read_bytes())
    h.update(pathlib.Path(scenes_path).read_bytes())
    h.update(str(supersample).encode())
    # the rendering code itself: a code change invalidates old renders, so a
    # cache written by a buggy intermediate version can never linger
    here = pathlib.Path(__file__).parent
    for source in ('render.py', 'video.py', 'play.py', 'frame.py'):
        h.update((here / source).read_bytes())
    for stage in stages:
        for m in stage.get('mappings', []):
            for name in render.media_paths(m):
                h.update(name.encode())
                _hash_file(h, cfg['_dir'] / name)
    return h.hexdigest()


def _manifest_valid(fp, stages):
    if not MANIFEST.exists():
        return False
    try:
        data = yaml.safe_load(MANIFEST.read_text())
    except Exception:
        return False
    total = len(stages)
    return (
        data.get('fingerprint') == fp
        and data.get('stages') == total
        and all(_stage_file(i).exists() for i in range(total))
        and all(_alpha_file(i).exists() for i, s in enumerate(stages) if _has_video(s))
        and all(_molding_file(k).exists() for k in _molding_keys(stages))
    )


def _clear_cache():
    """Remove all cached renders and the saved position.

    Called whenever the cache turns out stale at startup (manifest missing
    or mismatched, or any stage file gone), so no leftovers from an older
    show can linger.
    """
    if not CACHE_DIR.exists():
        return
    for pattern in ('stage-*.png', 'molding-*.png'):
        for path in CACHE_DIR.glob(pattern):
            path.unlink(missing_ok=True)
    MANIFEST.unlink(missing_ok=True)


def crossfade(
    screen,
    lights,
    get_src,
    get_dst,
    mold_src,
    mold_dst,
    duration,
    clock,
    levels,
    overlay,
    pump,
):
    """Fade between two live stages under the lamps, which mix them. The
    getters are re-evaluated every tick, so videos keep playing (and start
    playing) during the fade; `levels` (called per tick) is what the desk
    asks."""
    if duration > 0:
        start = pygame.time.get_ticks()
        while True:
            t = (pygame.time.get_ticks() - start) / 1000.0 / duration
            if t >= 1.0 or pump():  # pump: True = cut this fade short
                break
            lights.light(screen, get_src(), mold_src, levels(), fade=(get_dst(), mold_dst, t))
            overlay()
            pygame.display.flip()
            clock.tick(60)
    lights.light(screen, get_dst(), mold_dst, levels())
    overlay()
    pygame.display.flip()


def run(
    screen=None,
    config='config.yaml',
    scenes_path='scenes.yaml',
    supersample=3,
    test=False,
    dmx_source=None,
):
    """Run the app; with a screen provided, reuse it (menu mode).
    `dmx_source` overrides dmx.source (the --dmx flag)."""
    global _worker
    _stop_worker()
    cfg = render.load_config(config)
    if dmx_source:
        cfg['dmx'] = {**(cfg.get('dmx') or {}), 'source': dmx_source}
    scenes = render.load_scenes(scenes_path)
    stages, fades = render.parse_stages(scenes, cfg)
    if not stages:
        sys.exit('no stages in scenes file')
    total = len(stages)
    desk = dmx.Desk(cfg)

    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Present — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Present — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    pygame.key.set_repeat()  # no repeat: one stage per key press
    font = ui.help_font()
    clock = pygame.time.Clock()
    lights = lamps.Lamps(cfg)
    panel = faders.Faders(desk, pygame.font.SysFont('monospace', 16))

    screen.fill((0, 0, 0))
    ui.draw_help(screen, font, ['preparing stages...'])
    pygame.display.flip()

    RENDER_DONE = pygame.event.custom_type()
    fp = _fingerprint(config, scenes_path, cfg, stages, supersample)
    images = {}  # stage index -> static render (video polygons black)
    alphas = {}  # stage index -> alpha layer, only for stages with video
    moldings = {}  # molding key -> molding-only render (splits pictures from molding)
    players = {}  # stage index -> video.StagePlayer, created lazily
    clip_pool = {}  # path -> VideoClip, shared so timelines survive stage changes
    idx = 0

    if _manifest_valid(fp, stages):
        # valid cached renders: skip rendering
        print('cached renders match')
        for key in _molding_keys(stages):
            data = cv2.imread(str(_molding_file(key)), cv2.IMREAD_COLOR)
            moldings[key] = cv2.cvtColor(data, cv2.COLOR_BGR2RGB)
        for i, stage in enumerate(stages):
            if _has_video(stage):
                alphas[i] = cv2.imread(str(_alpha_file(i)), cv2.IMREAD_GRAYSCALE)
            data = cv2.imread(str(_stage_file(i)), cv2.IMREAD_COLOR)
            images[i] = cv2.cvtColor(data, cv2.COLOR_BGR2RGB)
    else:
        # first stage synchronously (incl. the one-off border shading), the
        # rest in a background thread; every result is cached on disk and the
        # manifest written when complete, so a restart can skip all the
        # rendering
        print(f'rendering {total} stages...')
        _clear_cache()
        CACHE_DIR.mkdir(exist_ok=True)
        renderer = render.StageRenderer(cfg, ss=supersample)

        def render_stage(i):
            """Render + cache stage i. The alpha goes in before images[i]:
            its presence is what marks the stage as available."""
            image = renderer.render(stages[i])
            render.save_png(image, _stage_file(i))
            alpha = renderer.video_alpha(stages[i])
            if alpha is not None:
                cv2.imwrite(str(_alpha_file(i)), alpha)
                alphas[i] = alpha
            key = _molding_key(stages[i])
            if key != 'blackout' and key not in moldings:
                moldings[key] = renderer.render_molding(stages[i])
                render.save_png(moldings[key], _molding_file(key))
            images[i] = image
            print(f'  {i + 1}/{total}: {stages[i].get("name", "?")}')

        render_stage(0)

        stop = threading.Event()

        def worker():
            for i in range(1, total):
                if stop.is_set():
                    print('render cancelled')
                    return
                render_stage(i)
                pygame.event.post(pygame.event.Event(RENDER_DONE, index=i))
            # only a complete, uncancelled render may declare the cache valid
            if not stop.is_set():
                MANIFEST.write_text(yaml.safe_dump({'fingerprint': fp, 'stages': total}))
                print('render cache complete')

        thread = threading.Thread(target=worker, daemon=True)
        _worker = (thread, stop)
        thread.start()

    info = False

    def now():
        return pygame.time.get_ticks() / 1000.0

    def get_player(i):
        """Player of stage i, created on first use (None while the stage is
        still rendering). Creating one composites all slides of its
        slideshows, so prepare() does it ahead of time; otherwise it would
        happen at the start of the crossfade into the stage."""
        if i not in players and i in images:
            players[i] = video.StagePlayer(cfg, stages[i], images[i], alphas.get(i), clip_pool)
        return players.get(i)

    def prepare(i):
        if any('slideshow' in m for m in stages[i].get('mappings', [])):
            get_player(i)

    for i in list(images):  # what is ready now (all, when the cache matched)
        prepare(i)

    def frame_for(i):
        """Current image of stage i (None while it is still rendering); a
        stage with video composites its due frame in first."""
        player = get_player(i)
        if player is None:
            return None
        return player.tick(now())[0]

    def molding_for(i):
        """Image with only stage i's molding; one object per distinct
        molding, so a crossfade can see that both sides have the same."""
        key = _molding_key(stages[i])
        if key == 'blackout' and key not in moldings:
            moldings[key] = np.zeros((cfg['canvas'][1], cfg['canvas'][0], 3), np.uint8)
        return moldings[key]

    shown = None  # the levels last drawn: a static stage redraws when the desk moves

    def step_slide(delta):
        """Step the current stage's slideshows one picture on or back."""
        player = players.get(idx)
        for clip in player.clips.values() if player else ():
            if isinstance(clip, video.SlideshowClip) and clip.t0 is not None and clip.count > 1:
                clip.t0 = now() - clip.step(now() - clip.t0, delta)
        show(idx)

    def clip_states(i):
        """[(a fade is running, seconds left, the whole stretch)] for stage
        i's slideshows and videos, in the order the stage maps them: a bar
        per distinct timing (clips in step share one)."""
        player = players.get(i)
        out, seen = [], set()
        for spec in player.specs if player else ():
            clip = player.clips[spec['path']]
            if clip.t0 is None:
                continue
            state = clip.next_change(now() - clip.t0)
            if state is None:
                continue
            key = (round(state[1], 1), state[2])  # in step: the same countdown
            if key not in seen:
                seen.add(key)
                out.append(state)
        return out

    def overlay():
        """The faders (Tab), the info lines (H, progress) and the state
        bar (top right) on top of the lit stage."""
        top = panel.draw(screen) if panel.visible else 0
        lines = []
        if info:
            lines.append(f'stage {idx + 1}/{total}: {stages[idx].get("name", "?")}')
            lines += desk.status_lines()
        if len(images) < total:
            lines.append(f'rendering {len(images)}/{total}...')
        if lines:
            ui.draw_help(screen, font, lines, top=top + 20)
        right = screen.get_width()

        max_blocks = right // (4 * STATE_PITCH)  # a bar stays within a quarter

        def bar(row, colors, left, span=0.0, on_left=False, unit=None):
            """One bar from a top corner: the corner block in colors[0],
            then one per step of what is left in colors[1]. The step comes
            from `span`, the whole stretch, so it stays the same while the
            bar shrinks."""
            unit = unit or bar_unit(max(span, left, 0.0), max_blocks)
            for k in range(1 + math.ceil(max(left, 0.0) / unit - 1e-3)):
                x = STATE_PITCH * k if on_left else right - STATE_PX - STATE_PITCH * k
                pygame.draw.rect(
                    screen,
                    colors[0] if k == 0 else colors[1],
                    (x, row * STATE_PITCH, STATE_PX, STATE_PX),
                )

        if transition_until is not None:  # the stage, top right
            bar(0, STATE_FADE, transition_until - now(), transition_span)
        elif hold_until is not None:
            bar(0, STATE_HOLD, hold_until - now(), stages[idx].get('hold') or 0.0)
        else:
            waits_for_desk = master_dark and _is_blackout(stages[idx]) and idx + 1 < total
            bar(0, STATE_DESK if waits_for_desk else STATE_KEY, 0.0)
        clips = clip_states(idx)  # the clips, top left, on one shared step
        if clips:
            unit = bar_unit(max(max(span, left) for _, left, span in clips), max_blocks)
            for row, (_, left, _) in enumerate(clips):
                bar(row, STATE_CLIP, left, on_left=True, unit=unit)

    def pump():
        """Events during a fade: the faders (Tab, mouse) keep working and a
        quit request is kept. A step key does nothing the first time, it is
        only remembered; the same key again within DOUBLE_PRESS seconds
        cuts the fade short and steps on, which is what this returns."""
        nonlocal pressed, cut
        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB:
                panel.toggle()
            elif event.type == pygame.KEYDOWN and event.key in STEP_KEYS:
                if pressed and pressed[0] == event.key and now() - pressed[1] <= DOUBLE_PRESS:
                    cut, pressed = STEP_KEYS[event.key], None
                else:
                    pressed = (event.key, now())
            elif event.type == pygame.QUIT:
                pygame.event.post(event)
            elif panel.visible:
                panel.handle(event)
        return cut is not None

    def show(i):
        nonlocal shown, last_shown
        shown = desk.levels()
        last_shown = now()
        lights.light(screen, frame_for(i), molding_for(i), shown)
        overlay()
        pygame.display.flip()

    def wait_for(i):
        """Block until the background render delivers stage i."""
        if frame_for(i) is None:
            ui.draw_help(screen, font, [f'waiting for stage {i + 1}...'])
            pygame.display.flip()
            while frame_for(i) is None:
                event = pygame.event.wait()
                if event.type == pygame.QUIT:
                    return False
        return True

    hold_until = None  # wall-clock seconds at which the stage's hold ends
    last_shown = 0.0  # when show() last drew (the bar's redraw during a hold)
    transition_until = None  # ... at which the running fade ends (the state bar)
    transition_span = 0.0  # how long that fade is, for the bar's step
    pressed = None  # (key, when) of a step key pressed during a fade
    cut = None  # +1/-1 when a second press asked to step on at once

    master_seen = set()  # 'up' / 'down': how the master has stood on this stage
    master_dark = False  # the master is at 0 (the desk's blue state block)

    def check_master():
        """The master steps in and out of blackout stages (see the module
        doc): into the blackout that follows a stage waiting for a key when
        the master goes to 0 there, out of a blackout waiting for a key when
        it comes up after having been 0 there."""
        nonlocal master_dark
        if not desk.receiving:
            return
        was, master_dark = master_dark, desk.master() < DARK
        master_seen.add('down' if master_dark else 'up')
        if master_dark != was:
            show(idx)  # the state block changes colour
        if hold_until is not None or idx + 1 >= total:
            return
        here, after = _is_blackout(stages[idx]), _is_blackout(stages[idx + 1])
        if not here and after and master_dark and 'up' in master_seen:
            go_to(idx + 1, instant=True)
        elif here and not master_dark and 'down' in master_seen:
            go_to(idx + 1, instant=True)

    def arm_hold():
        """Start the current stage's hold, if it has one and a next stage."""
        nonlocal hold_until
        master_seen.clear()  # a new stage: the master starts over
        hold = stages[idx].get('hold')
        hold_until = now() + hold if hold is not None and idx + 1 < total else None

    def hold_wait_ms():
        """Milliseconds to sleep on the event queue: to the end of the hold,
        at most BAR_TICK_MS so the state bar keeps shrinking; 0 = no hold,
        sleep until an event."""
        if hold_until is None:
            return 0
        return max(1, min(int((hold_until - now()) * 1000) + 1, BAR_TICK_MS))

    def check_hold():
        if hold_until is not None and now() >= hold_until:
            go_to(idx + 1)

    def go_to(new, instant=False):
        """Move to stage `new`, fading (`instant`: at once). A double press
        during the fade cuts it short and carries on in that direction (see
        pump), so the show can be stepped through faster than the fades
        allow."""
        nonlocal idx, transition_until, transition_span, pressed, cut
        while 0 <= new < total and new != idx:
            if not wait_for(new):
                return
            pressed, cut = None, None
            go_once(new, instant)
            if cut is None:
                return
            new, cut = idx + cut, None

    def go_once(new, instant=False):
        nonlocal idx, transition_until, transition_span
        fade = 0.0 if instant else fades[min(idx, new)]
        transition_until, transition_span = now() + fade, fade
        if _is_blackout(stages[new]) and not _is_blackout(stages[idx]):
            dim(
                screen,
                lights,
                lambda: frame_for(idx),
                molding_for(idx),
                True,
                fade,
                clock,
                desk.levels,
                overlay,
                pump,
            )  # lights out
        elif _is_blackout(stages[idx]) and not _is_blackout(stages[new]):
            dim(
                screen,
                lights,
                lambda: frame_for(new),
                molding_for(new),
                False,
                fade,
                clock,
                desk.levels,
                overlay,
                pump,
            )  # lights on
        else:
            crossfade(
                screen,
                lights,
                lambda: frame_for(idx),
                lambda: frame_for(new),
                molding_for(idx),
                molding_for(new),
                fade,
                clock,
                desk.levels,
                overlay,
                pump,
            )
        # step keys pressed during the fade went to pump(), not to the
        # queue: one press does nothing, two of the same carry on. What the
        # last tick left in the queue is dropped here
        pygame.event.clear(pygame.KEYDOWN)
        old, idx = idx, new
        if old in players:
            keep = players[idx].paths() if idx in players else set()
            players[old].stop(keep)  # clips the new stage also maps run on
        transition_until = None
        arm_hold()  # before the draw, so the bar shows the hold at once
        show(idx)

    arm_hold()
    show(idx)

    if test:
        if total > 1:
            go_to(1)
        vi = next((i for i in range(total) if _has_video(stages[i])), None)
        if vi is not None and wait_for(vi):
            idx = vi  # jump straight there, skipping the crossfade
            for _ in range(8):
                show(idx)
                pygame.time.wait(60)
            clips = get_player(idx).clips.values()
            frames = [c.pos + 1 for c in clips if isinstance(c, video.VideoClip)]
            slides = [c.pos for c in clips if isinstance(c, video.SlideshowClip)]
            print(
                f'animated stage "{stages[vi].get("name", "?")}": '
                f'video frames {frames}, slideshow states {slides}'
            )
        # the lamps: object 1 canvas half / molding full, 2 the other way
        # round, 3 out, warm light; timed, and kept to look at
        demo = dmx.Levels(
            {
                oid: ((0.5, 1.0, 2600.0), (1.0, 0.5, 2600.0), (0.0, 0.0, 2600.0))[k % 3]
                for k, oid in enumerate(desk.ids)
            }
        )
        out = pygame.Surface(
            cfg['canvas']
        )  # not the screen: a dummy display may not be canvas-sized
        t0 = pygame.time.get_ticks()
        for _ in range(20):
            lights.light(out, frame_for(idx), molding_for(idx), demo)
        ms = (pygame.time.get_ticks() - t0) / 20.0
        pathlib.Path('_renders').mkdir(exist_ok=True)
        pygame.image.save(out, '_renders/lamps.png')
        print(f'lamps: {ms:.1f} ms per frame; {desk.status()} (written: _renders/lamps.png)')
        desk.close()
        if standalone:
            pygame.quit()
        print('self-test ok')
        return

    running = True

    def handle(event):
        nonlocal running, info
        if event.type == pygame.QUIT:
            running = False
        elif event.type == RENDER_DONE:
            prepare(event.index)  # slides composited while the show is idle
            show(idx)  # refresh the progress line
        elif panel.visible and panel.handle(event):
            show(idx)
        elif event.type == pygame.KEYDOWN:
            if panel.visible and event.key in (pygame.K_TAB, pygame.K_ESCAPE):
                panel.toggle()  # Esc closes the faders first (a stray Tab)
                show(idx)
            elif event.key in ui.QUIT_KEYS:
                running = False
            elif event.key == pygame.K_TAB:
                panel.toggle()
                show(idx)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE, pygame.K_RIGHT):
                go_to(idx + 1)
            elif event.key in (pygame.K_BACKSPACE, pygame.K_LEFT):
                go_to(idx - 1)
            elif event.unicode in ('<', '>') or event.key in (pygame.K_COMMA, pygame.K_PERIOD):
                step_slide(1 if event.unicode == '>' or event.key == pygame.K_PERIOD else -1)
            elif event.key == pygame.K_h:
                info = not info
                show(idx)
        elif event.type in (pygame.VIDEOEXPOSE, pygame.WINDOWEXPOSED):
            show(idx)

    # a stage with video redraws continuously; a static stage sleeps on the
    # event queue like before
    while running:
        player = get_player(idx)
        if player is not None and player.animated:
            for event in pygame.event.get():
                handle(event)
                if not running:
                    break
            if running:
                show(idx)
                clock.tick(60)
        elif desk.on or panel.visible:
            # a static stage under the desk (or the faders): look every
            # 16 ms whether the levels moved (NOEVENT on timeout, which
            # handle() ignores); the faders redraw either way
            handle(pygame.event.wait(16))
            if running and (panel.visible or desk.levels() != shown):
                show(idx)
        else:
            # sleep on the event queue, at most until the stage's hold ends
            handle(pygame.event.wait(hold_wait_ms()))
        if running:
            check_hold()
            check_master()
            if hold_until is not None and now() - last_shown >= BAR_TICK_MS / 1000.0:
                show(idx)  # the state bar shrinks with the hold

    desk.close()
    pygame.mouse.set_visible(False)
    if standalone:
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description='Play the stages on the projector')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--scenes', default='scenes.yaml')
    ap.add_argument('--supersample', type=int, default=3)
    ap.add_argument(
        '--test', action='store_true', help='self-test: show first stage, one fade, quit'
    )
    ap.add_argument(
        '--dmx',
        choices=dmx.SOURCES,
        help='override dmx.source from config.yaml (demo = a scripted desk)',
    )
    args = ap.parse_args()
    run(
        config=args.config,
        scenes_path=args.scenes,
        supersample=args.supersample,
        test=args.test,
        dmx_source=args.dmx,
    )


if __name__ == '__main__':
    main()
