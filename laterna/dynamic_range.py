#!/usr/bin/env python
"""Dynamic range and gamma check for the projector (step 2).

Proven test patterns, all available in the same colours as the test
patterns:

  1  near-black steps (PLUGE-like): labelled bars 0..20/255 on black — if
     the 1 and 2 bars merge with 0, the chain crushes blacks
  2  near-white steps: labelled bars 224..255 on full colour — clipping
     check at the light end
  3  staircase (32 steps) plus a continuous ramp — banding and overall
     dynamic range
  4  gamma chart (line-interleave method, cf. lagom.nl / Norman Koren):
     three solid patches inside a stripe field that averages to 50% light.
     The MIDDLE patch is the null indicator: adjust the gamma correction
     (up/down) until it blends into the stripes; the outer two (slightly
     low and slightly high on purpose) should then deviate symmetrically —
     they show direction and sensitivity. Only valid at exact 1:1 pixels:
     judge on the projector, not on a scaled laptop screen. The stripes are
     fixed at 1 px — that is how the method is defined; thicker pitches
     measurably shift the result (panel and processing effects), as seen
     in practice.

  R / G / B / W  colour: red / green / blue / white
  C / M / Y / O  colour: cyan / magenta / yellow / orange
  H  help overlay on/off
  Q / ESC  quit (back to the menu when started from main.py)

Gamma correction (closed loop): every pattern here is shown through the
same LUT that play.py applies (render.gamma_lut, target 2.2). Adjust the
measured gamma with the up/down arrows (Shift = coarse) until the 2.2 patch
blends into its stripes, then S saves `gamma:` to config.yaml. T switches
the correction off again. Measure with white first; check R/G/B afterwards
— when the channels clearly disagree, put `gamma: [r, g, b]` in the config
by hand.
"""

import argparse

import cv2
import numpy as np
import pygame

from . import calibration, render, ui
from .test_pattern import COLOR_KEYS, COLOR_NAMES, WHITE

LABEL = (90, 90, 90)

NEAR_BLACK = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20]
NEAR_WHITE = [255, 254, 253, 252, 250, 248, 244, 240, 232, 224]
# middle = the null indicator (target); outer two deliberately off
GAMMAS = [2.0, 2.2, 2.4]

PATTERN_KEYS = {
    pygame.K_1: 'near-black',
    pygame.K_KP_1: 'near-black',
    pygame.K_2: 'near-white',
    pygame.K_KP_2: 'near-white',
    pygame.K_3: 'staircase',
    pygame.K_KP_3: 'staircase',
    pygame.K_4: 'gamma',
    pygame.K_KP_4: 'gamma',
}


def _scaled(color, value):
    """The colour at brightness value (0..255 along the channel)."""
    return tuple(int(round(c * value / 255.0)) for c in color)


def _label(image, text, x, y, dim=False):
    cv2.putText(
        image,
        text,
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (40, 40, 40) if dim else LABEL,
        2,
        cv2.LINE_AA,
    )


def _steps(image, values, color, background):
    """Labelled bars over the middle of the canvas."""
    h, w = image.shape[:2]
    image[:] = background
    n = len(values)
    bar = w // (n + 1)
    for i, v in enumerate(values):
        x = bar // 2 + i * bar
        image[h // 4 : 3 * h // 4, x : x + bar - 24] = _scaled(color, v)
        _label(image, str(v), x, 3 * h // 4 + 40, dim=background != (0, 0, 0))


def near_black(w, h, color):
    image = np.zeros((h, w, 3), np.uint8)
    _steps(image, NEAR_BLACK, color, (0, 0, 0))
    return image


def near_white(w, h, color):
    image = np.zeros((h, w, 3), np.uint8)
    _steps(image, NEAR_WHITE, color, _scaled(color, 255))
    return image


def staircase(w, h, color):
    """32 steps on top, a continuous ramp below."""
    image = np.zeros((h, w, 3), np.uint8)
    xs = np.arange(w)
    steps = np.round((xs * 32 // w) * 255.0 / 31.0).clip(0, 255)
    ramp = xs * 255.0 / (w - 1)
    channel = np.asarray(color, np.float32) / 255.0
    image[: h // 2] = (steps[:, None] * channel).astype(np.uint8)
    image[h // 2 :] = (ramp[:, None] * channel).astype(np.uint8)
    return image


def gamma_chart(w, h, color):
    """Line-interleave gamma chart: one 50%-light stripe field with three
    solid patches; the middle one is the null indicator. Stripes are 1 px
    (the method's definition); the field height is even so the average is
    exactly 50%."""
    image = np.zeros((h, w, 3), np.uint8)
    n = len(GAMMAS)
    field_w = 2 * w // 3
    fx0 = (w - field_w) // 2
    y0 = h // 6
    y1 = y0 + ((5 * h // 6 - y0) // 2) * 2
    image[y0:y1:2, fx0 : fx0 + field_w] = color  # alternate lines: colour / black
    col = field_w // n
    my = (y1 - y0) // 4
    for i, g in enumerate(GAMMAS):
        solid = _scaled(color, round(255.0 * 0.5 ** (1.0 / g)))
        mx = col // 4
        x0 = fx0 + i * col
        image[y0 + my : y1 - my, x0 + mx : x0 + col - mx] = solid
        middle = i == n // 2
        _label(
            image,
            f'{g:.1f}' + (' = target' if middle else ''),
            x0 + col // 2 - (70 if middle else 24),
            y1 + 44,
        )
    _label(
        image, 'adjust gamma (up/down) until the MIDDLE patch blends into the stripes', fx0, h - 24
    )
    return image


PATTERNS = {
    'near-black': near_black,
    'near-white': near_white,
    'staircase': staircase,
    'gamma': gamma_chart,
}


def self_test(cfg):
    """Headless check of the LUT math and the chart; does not save."""
    assert render.gamma_lut({'objects': []}) is None
    assert render.gamma_lut({'gamma': render.GAMMA_TARGET, 'objects': []}) is None
    lut = render.gamma_lut({'gamma': 2.5, 'objects': []})
    assert lut.shape == (256, 3) and lut[0, 0] == 0 and lut[255, 0] == 255
    assert all(np.all(np.diff(lut[:, c].astype(int)) >= 0) for c in range(3))
    # measured 2.5, target 2.2: mid-values must come out brighter
    assert lut[128, 0] > 128
    image = gamma_chart(*cfg['canvas'], (255, 255, 255))
    render.apply_gamma(image, lut)
    render.save_png(image, 'renders/dynamic-range.png')
    print('self-test ok (written: renders/dynamic-range.png)')


def run(screen=None, config='config.yaml'):
    """Run the app; with a screen provided, reuse it (menu mode)."""
    cfg = render.load_config(config)
    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Dynamic range — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Dynamic range — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    font = ui.help_font()
    clock = pygame.time.Clock()
    w, h = cfg['canvas']

    pattern = 'near-black'
    color = WHITE
    show_help = True
    dirty = False
    message = ''

    def show():
        image = PATTERNS[pattern](w, h, color)
        # closed loop: view the patterns through the same correction the
        # renderer applies, so the chart verifies the saved value
        render.apply_gamma(image, render.gamma_lut(cfg))
        screen.blit(ui.to_surface(image), (0, 0))
        if show_help:
            measured = cfg.get('gamma')
            state = f'{float(measured):.2f}' if measured else 'off'
            ui.draw_help(
                screen,
                font,
                [
                    f'pattern: {pattern}   colour: {COLOR_NAMES[color]}'
                    + f'   gamma correction: {state}'
                    + ('   * unsaved changes *' if dirty else ''),
                    '1 near-black  2 near-white  3 staircase  4 gamma',
                    'up/down adjust gamma until the 2.2 patch blends (Shift = coarse)',
                    'T correction off  S save  R/G/B/W C/M/Y/O colour  H help  Q quit',
                ]
                + ([message] if message else []),
            )
        pygame.display.flip()

    show()
    running = True
    while running:
        event = pygame.event.wait()
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            d_gamma = 0.1 if event.mod & pygame.KMOD_SHIFT else 0.02
            message = ''
            if event.key in ui.QUIT_KEYS:
                running = False
            elif event.key in PATTERN_KEYS:
                pattern = PATTERN_KEYS[event.key]
            elif event.key in COLOR_KEYS:
                color = COLOR_KEYS[event.key]
            elif event.key == pygame.K_UP:
                cfg['gamma'] = round(float(cfg.get('gamma') or render.GAMMA_TARGET) + d_gamma, 2)
                dirty = True
            elif event.key == pygame.K_DOWN:
                cfg['gamma'] = round(
                    max(0.5, float(cfg.get('gamma') or render.GAMMA_TARGET) - d_gamma), 2
                )
                dirty = True
            elif event.key == pygame.K_t:
                cfg.pop('gamma', None)
                dirty = True
                message = 'gamma correction off'
            elif event.key == pygame.K_s:
                calibration.save_config(cfg, config)
                dirty = False
                message = f'saved to {config}'
            elif event.key == pygame.K_h:
                show_help = not show_help
            show()
        elif event.type in (pygame.VIDEOEXPOSE, pygame.WINDOWEXPOSED):
            show()
        clock.tick(60)

    if standalone:
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description='Dynamic range and gamma check')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument(
        '--test', action='store_true', help='self-test: LUT math and one chart render, no display'
    )
    args = ap.parse_args()
    if args.test:
        self_test(render.load_config(args.config))
        return
    run(config=args.config)


if __name__ == '__main__':
    main()
