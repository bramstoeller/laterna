"""Renders the application icon with the projection's own renderer: an arch
as an abstract sign — a gilded molding, drawn heavier than a real one so
it reads at small sizes, around the dark fill lit by the spot from the
example show's config.yaml, no picture; an arch of many pieces, so it is a plain round
arch — on a transparent background.

    python -m tools.make_icon

Writes laterna/icon/laterna.png (512 px) and laterna/icon/laterna-256.png;
the desktop
launcher, the window icon (laterna/ui.py) and the build point at the first.
"""

import copy
import pathlib

import cv2
import numpy as np

from laterna import frame, render

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'laterna' / 'icon'
SIZE = 1024  # render size (px); the icon is downscaled from it
MARGIN = 0.06  # empty border around the frame, fraction of SIZE
BORDER = 220.0  # molding width (mm) in the icon; the real one is 94


def icon_config():
    """A one-object config: a heavy-bordered arch filling a square canvas."""
    cfg = copy.deepcopy(render.load_config(ROOT / 'shows' / 'example' / 'config.yaml'))
    OUT.mkdir(exist_ok=True)
    inner = frame.arch_inner(2098.0, 1500.0 + 99.0, 1500.0 + 99.0, 64, 2098.0 * 1.5708 / 64, BORDER)
    arch = dict(border=BORDER, inner=inner.tolist())
    h_mm = float(frame.build(arch)['polygons'][0]['points'][:, 1].max())
    mmpp = h_mm / (SIZE * (1 - 2 * MARGIN))  # the frame's height fills the square
    cfg['canvas'] = [SIZE, SIZE]
    cfg['scale_mm_per_px'] = mmpp
    cfg['rotation'] = 0.0
    cfg['image_offset'] = [0.0, -MARGIN * SIZE * mmpp]  # lift the frame off the bottom edge
    cfg['objects'] = [
        dict(id=1, name='icon', frame=arch, origin=[0.0, 0.0], scale=1.0, rotation=0.0)
    ]
    return cfg


def main():
    cfg = icon_config()
    renderer = render.SceneRenderer(cfg, ss=3)
    rgb = renderer.render({'name': 'icon', 'mappings': []})  # fill colour + spot, no picture
    rgb = render.full_power(rgb, cfg)  # the icon is a still: no lamps to give the headroom back
    # alpha: the frame's silhouette, downsampled like the colours for a soft edge
    silhouette = render._fill_mask(renderer.shape, render.polys_px(cfg, None, 3)).astype(np.float32)
    alpha = cv2.resize(silhouette, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    rgba = np.dstack([rgb, np.clip(alpha * 255, 0, 255).astype(np.uint8)])
    for size, name in ((512, 'laterna.png'), (256, 'laterna-256.png')):
        small = cv2.resize(rgba, (size, size), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(OUT / name), cv2.cvtColor(small, cv2.COLOR_RGBA2BGRA))
        print(f'written: {OUT.relative_to(ROOT) / name}')


if __name__ == '__main__':
    main()
