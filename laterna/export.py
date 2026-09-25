#!/usr/bin/env python
"""Export: three PDFs of the loaded config.yaml and scenes.yaml, written to
_export/ next to config.yaml (laterna/documents.py lays them out):

  config.pdf     calibration, the frame shapes with their inner corners,
                 molding, light and look, the pretend spot (white canvases
                 with and without it and a map of its strength), a scene
                 with and without it, DMX, and config.yaml itself
  run-sheet.pdf  the operator's cue list: keys, state blocks, what to do
                 when something goes wrong, then every scene with its
                 picture, how it is reached (key or by itself, fade, time
                 since the last key), what it does while it stands, what is
                 on each object and its page in the backup
  backup.pdf     every scene full screen at the canvas's aspect ratio, in
                 show order, to show from a PDF viewer when all else fails:
                 blackouts as black pages, a slideshow as one page per
                 distinct combination of its pictures, a video as its
                 first frame; with the keystone warp of config.yaml, like
                 the presentation (the other two show the plane)

Everything is rendered fresh with the presentation's renderer and shown as
the lamps show it with the desk at full (laterna/lamps.py), so the export
follows whatever config and scenes are loaded; nothing is taken from the
render cache.

Menu mode (step 6) shows the progress; Q / Esc cancels. From the command
line it runs headless:

  python -m laterna.export [--config config.yaml] [--scenes scenes.yaml] [--out export]
"""

import argparse
import copy
import math
import pathlib

import cv2
import numpy as np
import pygame

from . import dmx, documents, render, ui, video
from .look import WHITE_VIEW

EXPORT_DIR = '_export'
MAX_COMBINATIONS = 64  # slideshow pictures per scene in the export
THUMB_WIDTH = 480  # px of the cue list pictures


class Cancelled(Exception):
    pass


def full_light(cfg):
    """The gain (3,) the lamps apply with the desk at full: the headroom
    back, in the colour of the light (lamps.lamp_gain at level 1)."""
    look = cfg['look']
    return render.headroom_gain(cfg) * render.white_gain(look['temperature'], look['white'])


def lit(image, gain):
    return np.clip(image.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def crop_box(cfg):
    """(x0, y0, x1, y1) px around all frames with a small margin: what the
    cue list and the config pages show of the canvas."""
    w, h = cfg['canvas']
    pts = np.vstack(render.polys_px(cfg))
    margin = 0.03 * w
    x0, y0 = np.floor(pts.min(axis=0) - margin).astype(int)
    x1, y1 = np.ceil(pts.max(axis=0) + margin).astype(int)
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


# --- slideshows ---------------------------------------------------------


def slide_combinations(timings, limit=MAX_COMBINATIONS):
    """[(t, (k per slideshow))]: the distinct combinations of pictures a
    scene's slideshows show, in the order they first come up, with the
    seconds after entering the scene at which each starts to fade in.
    timings = [(count, hold, transition_time)] (video.build_specs). A
    picture counts from the start of its fade-in, as SlideshowClip.step
    counts it."""
    if not timings:
        return [(0.0, ())]
    slots = [float(hold) + float(tt) for _, hold, tt in timings]

    def current(t):
        return tuple(
            int((t + tt) // slot) % n if slot > 0 else 0 for (n, _, tt), slot in zip(timings, slots)
        )

    # every combination has come up within the product of the periods
    horizon = min(math.prod(n * s for (n, _, _), s in zip(timings, slots) if s > 0), 24 * 3600.0)
    events = {0.0}
    for (n, hold, _), slot in zip(timings, slots):
        if slot > 0 and n > 1:
            steps = min(int(math.ceil(horizon / slot)), 10000)
            events.update(j * slot + float(hold) for j in range(steps))
    out, seen = [], set()
    for t in sorted(events):
        combo = current(t + 1e-6)
        if combo not in seen:
            seen.add(combo)
            out.append((0.0 if not out else t, combo))
            if len(out) >= limit:
                break
    return out


def _stem(path):
    return str(path).replace('\\', '/').split('/')[-1].rsplit('.', 1)[0]


def scene_views(cfg, renderer, scene, gain, projector=None):
    """The pictures of a scene as the audience sees them at full light:
    [{'full': JPEG bytes of the whole canvas, warped to the projector by
    `projector` (a config with its keystone; None = as rendered), or None
    (blackout), 'thumb':
    RGB crop for the cue list or None, 't': seconds after entering,
    'changed': [picture names new in this view]}], one per distinct
    combination of slideshow pictures, videos at their first frame."""
    box = crop_box(cfg)
    if scene.get('blackout'):
        return [{'full': None, 'thumb': None, 't': 0.0, 'changed': []}]
    lut = render.gamma_lut(cfg)
    base = renderer.render(scene)
    alpha = renderer.video_alpha(scene)
    specs = video.build_specs(cfg, scene, base, alpha) if alpha is not None else []
    by_path = {s['path']: s for s in specs}
    shows, fixed = [], []  # (spec, mapping) of slideshows; rows of videos
    for m in scene.get('mappings', []):
        if 'slideshow' in m and video._slideshow_key(m) in by_path:
            shows.append((by_path[video._slideshow_key(m)], m))
        elif 'video' in m and str(cfg['_dir'] / m['video']) in by_path:
            spec = by_path[str(cfg['_dir'] / m['video'])]
            clip = video.VideoClip(spec['path'])
            frame = clip.frame_at(0.0)
            if clip.ok:
                clip.cap.release()
            if frame is not None:
                fixed.append((spec, video._compose_rows(video._fit_region(frame, spec), spec, lut)))
    views, before = [], None
    for t, combo in slide_combinations([spec['timing'] for spec, _ in shows]):
        out = base.copy()
        flat = out.reshape(-1, 3)
        for spec, rows in fixed:
            flat[spec['flat']] = rows
        names = []
        for (spec, m), k in zip(shows, combo):
            rows, _ = video._slide_rows(spec, k, 0.0, lut)  # a video slide: its first frame
            flat[spec['flat']] = rows
            names.append(_stem(m['slideshow'][k]))
        changed = [n for j, n in enumerate(names) if before is None or before[j] != n]
        before = names
        image = lit(out, gain)
        full = image if projector is None else render.keystone_image(image, projector)
        ok, buf = cv2.imencode(
            '.jpg', cv2.cvtColor(full, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92]
        )
        thumb = documents.crop(image, box)
        if thumb.shape[1] > THUMB_WIDTH:
            th = max(1, round(thumb.shape[0] * THUMB_WIDTH / thumb.shape[1]))
            thumb = cv2.resize(thumb, (THUMB_WIDTH, th), interpolation=cv2.INTER_AREA)
        views.append({'full': buf.tobytes(), 'thumb': thumb, 't': t, 'changed': changed})
    for spec, _ in shows:
        for slide in spec['slides']:
            if isinstance(slide, dict) and slide['clip'].ok:
                slide['clip'].cap.release()
    return views


# --- the config ----------------------------------------------------------


def object_geometry(cfg):
    """Per object: its settings and outlines in world mm and px."""
    out = []
    for o in cfg['objects']:
        wood = render.object_wood_world(cfg, o)
        wood_px = render.world_to_px(wood, cfg)
        out.append(
            {
                'id': o['id'],
                'name': o.get('name', o['id']),
                'description': o.get('description'),
                'origin': o['origin'],
                'scale': o.get('scale', 1.0),
                'rotation': o.get('rotation', 0.0),
                'border': o['frame']['border'],
                'inner_local': o['frame']['inner'],
                'corners_px': render.world_to_px(render.object_corners_world(cfg, o), cfg),
                'wood': wood,
                'canvas': render.object_canvas_world(cfg, o),
                'polygons': render.object_polygons_world(cfg, o),
                'bbox_px': (wood_px.min(axis=0), wood_px.max(axis=0)),
            }
        )
    return out


def _widest(cfg):
    return max(cfg['objects'], key=lambda o: np.ptp(render.object_wood_world(cfg, o)[:, 0]))


def config_renders(cfg, renderer, scenes, gain):
    """The pictures of the config pages (documents.config_sheet)."""
    box = crop_box(cfg)
    look = cfg['look']
    example = next(
        (
            s
            for s in scenes
            if not s.get('blackout') and any('image' in m for m in s.get('mappings', []))
        ),
        None,
    )
    extras = {
        'look': copy.deepcopy(look),
        'crop': box,
        'white_spot': lit(renderer.render(WHITE_VIEW), gain),
        'scene_spot': lit(renderer.render(example), gain) if example else None,
        'scene_name': example.get('name', '?') if example else None,
    }
    # the same without the spots: strength 0, then back
    saved = copy.deepcopy(cfg['look'])
    cfg['look']['spot']['strength'] = 0.0
    if cfg['look'].get('molding_spot'):
        cfg['look']['molding_spot']['strength'] = 0.0
    renderer.apply_look()
    extras['white_flat'] = lit(renderer.render(WHITE_VIEW), gain)
    extras['scene_flat'] = lit(renderer.render(example), gain) if example else None
    cfg['look'] = saved
    renderer.apply_look()
    # the molding alone, top of the widest frame
    widest = _widest(cfg)
    wood_px = render.world_to_px(render.object_wood_world(cfg, widest), cfg)
    (bx0, by0), (bx1, by1) = wood_px.min(axis=0), wood_px.max(axis=0)
    mw, mh = bx1 - bx0, by1 - by0
    w, h = cfg['canvas']
    detail = (
        int(max(0, bx0 - 0.1 * mw)),
        int(max(0, by0 - 0.1 * mh)),
        int(min(w, bx1 + 0.1 * mw)),
        int(min(h, by0 + 0.6 * mh)),
    )
    extras['molding_detail'] = documents.crop(lit(renderer.render(None), gain), detail)
    # the spot's strength on the pictures, per pixel, as a false-colour map
    strength = np.zeros((h, w), np.float32)
    for o in cfg['objects']:
        ys, xs = np.nonzero(render.object_mask(cfg, o['id']))
        g = render.image_gain_at(cfg, [o['id']], xs, ys, 1)
        if g is not None:
            strength[ys, xs] = np.asarray(g).reshape(len(xs), -1).mean(axis=1)
    if strength.max() > 0:
        strength /= strength.max()
    colour = cv2.applyColorMap((strength * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    colour[strength == 0] = 0
    extras['gain_map'] = cv2.cvtColor(colour, cv2.COLOR_BGR2RGB)
    return extras


def dmx_info(cfg):
    """(settings, [(offset, [labels])]) of the desk input, or None."""
    if not cfg.get('dmx'):
        return None
    settings = dmx.settings(cfg)
    grouped = {}
    for offset, label in dmx.channel_labels(cfg, settings['channels']):
        grouped.setdefault(offset, []).append(str(label))
    return settings, sorted(grouped.items())


# --- the whole export ----------------------------------------------------


def export_all(
    config='config.yaml', scenes_path='scenes.yaml', out_dir=None, supersample=3, progress=print
):
    """Render and write the three PDFs; returns their paths. progress(text)
    is called between the steps (and may raise Cancelled)."""
    config, scenes_path = pathlib.Path(config), pathlib.Path(scenes_path)
    cfg = render.load_config(config)
    # the documents show the plane; only the backup, which goes on the
    # projector, gets the keystone warp (a lit render is warped like play.py
    # warps it, the lamps' gain being per pixel)
    projector = {'canvas': cfg['canvas'], 'keystone': cfg.pop('keystone', None)}
    cfg['look'] = render.look_settings(cfg)
    data = render.load_scenes(scenes_path)
    scenes, fades = render.parse_scenes(data, cfg)
    out = pathlib.Path(out_dir) if out_dir else config.resolve().parent / EXPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    source = f'{config.name} + {scenes_path.name}'

    progress('computing the molding...')
    renderer = render.SceneRenderer(cfg, ss=supersample)
    gain = full_light(cfg)
    views = []
    for i, scene in enumerate(scenes):
        progress(f'scene {i + 1}/{len(scenes)}: {scene.get("name", "?")}')
        views.append(scene_views(cfg, renderer, scene, gain, projector))
    progress('pictures for the config pages...')
    extras = config_renders(cfg, renderer, scenes, gain)

    paths = [out / 'backup.pdf', out / 'run-sheet.pdf', out / 'config.pdf']
    progress(f'writing {paths[0].name}...')
    firsts = documents.backup(paths[0], cfg, scenes, views)
    progress(f'writing {paths[1].name}...')
    documents.run_sheet(
        paths[1],
        cfg,
        scenes,
        fades,
        views,
        firsts,
        crop_box(cfg),
        source,
        data.get('description'),
    )
    progress(f'writing {paths[2].name}...')
    documents.config_sheet(
        paths[2], cfg, config.read_text(), object_geometry(cfg), extras, dmx_info(cfg), source
    )
    return paths


def run(screen=None, config='config.yaml', scenes_path='scenes.yaml', supersample=3):
    """Run the export with its progress on screen; with a screen provided,
    reuse it (menu mode). Q / Esc cancels, any key afterwards goes back."""
    standalone = screen is None
    if standalone:
        screen = ui.init_screen(render.load_config(config)['canvas'], f'Export — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Export — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    font = ui.help_font()
    done = []

    def draw(lines):
        screen.fill(ui.BLACK)
        ui.draw_help(screen, font, lines)
        pygame.display.flip()

    def progress(text):
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key in ui.QUIT_KEYS
            ):
                raise Cancelled
        done.append(text)
        draw(
            ['export: rendering every scene, then three PDFs', '']
            + done[-30:]
            + ['', 'Q / Esc cancels']
        )

    try:
        paths = export_all(config, scenes_path, supersample=supersample, progress=progress)
        lines = ['export done, written:'] + [f'  {p}' for p in paths]
    except Cancelled:
        lines = ['export cancelled (PDFs written before stay as they were)']
    draw(lines + ['', 'any key: back'])
    pygame.event.clear()
    while True:
        event = pygame.event.wait()
        if event.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            break
    if standalone:
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(
        description='Export config, run sheet and backup as PDF (headless)'
    )
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--scenes', default='scenes.yaml')
    ap.add_argument('--out', help=f'output folder (default: {EXPORT_DIR}/ next to the config)')
    ap.add_argument('--supersample', type=int, default=3)
    args = ap.parse_args()
    for path in export_all(args.config, args.scenes, args.out, args.supersample):
        print('written', path)


if __name__ == '__main__':
    main()
