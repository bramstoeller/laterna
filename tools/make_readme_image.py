"""Renders the picture in the README: the example show's `pictures` scene
as the audience sees it at full light, cropped around the frames.

    python -m tools.make_readme_image

Writes docs/example.jpg.
"""

import pathlib

import cv2

from laterna import export, render

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOW = ROOT / 'shows' / 'example'
OUT = ROOT / 'docs' / 'example.jpg'
SCENE = 'pictures'
WIDTH = 1280  # px of the written picture


def main():
    cfg = render.load_config(SHOW / 'config.yaml')
    cfg.pop('keystone', None)
    cfg['look'] = render.look_settings(cfg)
    scenes, _ = render.parse_scenes(render.load_scenes(SHOW / 'scenes.yaml'), cfg)
    scene = next(s for s in scenes if s.get('name') == SCENE)
    image = export.lit(render.SceneRenderer(cfg, ss=3).render(scene), export.full_light(cfg))
    x0, y0, x1, y1 = export.crop_box(cfg)
    image = image[y0:y1, x0:x1]
    h, w = image.shape[:2]
    image = cv2.resize(image, (WIDTH, round(h * WIDTH / w)), interpolation=cv2.INTER_AREA)
    OUT.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(OUT), cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f'written: {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
