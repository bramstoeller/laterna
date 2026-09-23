#!/usr/bin/env python
"""Test patterns for the projector (canvas size from config.yaml).

Patterns only — nothing is saved; alignment happens in configure.py.
Pattern and colour are selected independently:
  1  grid (line width adjustable with L: 1/2/3/4 px, default 2)
  2  blocks (checkerboard on black)
  3  solid
  0  black
  R / G / B / W  colour: red / green / blue / white
  C / M / Y / O  colour: cyan / magenta / yellow / orange
  +/-  grid/block size larger/smaller
       steps: all common divisors of the canvas width and height
       (1..120 px; 120 px = 16x9 squares)

Other:
  H  help overlay on/off
  Q / ESC  quit (back to the menu when started from main.py)
"""

import math

import numpy as np
import pygame

from . import render, ui

DEFAULT_STEP = 120  # px; 16x10 squares on 1920x1200 (16x9 on 1920x1080)

WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
CYAN = (0, 255, 255)
MAGENTA = (255, 0, 255)
YELLOW = (255, 255, 0)
ORANGE = (255, 128, 0)
BLACK = (0, 0, 0)

PATTERN_KEYS = {
    pygame.K_1: 'grid',
    pygame.K_KP_1: 'grid',
    pygame.K_2: 'blocks',
    pygame.K_KP_2: 'blocks',
    pygame.K_3: 'solid',
    pygame.K_KP_3: 'solid',
    pygame.K_0: 'black',
    pygame.K_KP_0: 'black',
}

COLOR_KEYS = {
    pygame.K_r: RED,
    pygame.K_g: GREEN,
    pygame.K_b: BLUE,
    pygame.K_w: WHITE,
    pygame.K_c: CYAN,
    pygame.K_m: MAGENTA,
    pygame.K_y: YELLOW,
    pygame.K_o: ORANGE,
}
COLOR_NAMES = {
    RED: 'red',
    GREEN: 'green',
    BLUE: 'blue',
    WHITE: 'white',
    CYAN: 'cyan',
    MAGENTA: 'magenta',
    YELLOW: 'yellow',
    ORANGE: 'orange',
}


def grid_steps(canvas):
    """Grid/block sizes in px; +/- cycles through them: all common divisors
    of the canvas size, so the squares always fit whole."""
    gcd = math.gcd(*canvas)
    return [d for d in range(1, gcd + 1) if gcd % d == 0]


LINE_WIDTHS = [1, 2, 3, 4]


def draw_grid(w, h, color, step, line=2):
    """Grid lines of `line` px on every block boundary, always inside the
    canvas; thicker than 1 px so a slightly unfocused projector still shows
    clean lines."""
    image = np.zeros((h, w, 3), np.uint8)
    image[(np.arange(h) % step) < line, :] = color
    image[:, (np.arange(w) % step) < line] = color
    # right and bottom edge lines fall inside the last block
    image[h - line :, :] = color
    image[:, w - line :] = color
    return image


def draw_blocks(w, h, color, step):
    """Checkerboard of colour on black with blocks of step x step px."""
    ys = (np.arange(h) // step) % 2
    xs = (np.arange(w) // step) % 2
    pattern = (ys[:, None] ^ xs[None, :]).astype(np.uint8)
    return pattern[:, :, None] * np.asarray(color, np.uint8)


def draw_solid(w, h, color):
    return np.full((h, w, 3), color, np.uint8)


def run(screen=None, config='config.yaml'):
    """Run the app; with a screen provided, reuse it (menu mode)."""
    canvas = tuple(render.load_config(config)['canvas'])
    standalone = screen is None
    if standalone:
        screen = ui.init_screen(canvas, f'Test pattern — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Test pattern — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    font = ui.help_font()
    clock = pygame.time.Clock()
    pygame.key.set_repeat(350, 40)  # hold a key to keep stepping
    w, h = canvas
    STEPS = grid_steps(canvas)

    pattern = 'grid'
    color = WHITE
    # the default step, or the largest one that fits below it
    step_idx = max(i for i, s in enumerate(STEPS) if s <= DEFAULT_STEP)
    line_idx = LINE_WIDTHS.index(2)
    show_help = True

    def show():
        step = STEPS[step_idx]
        if pattern == 'black':
            image = draw_solid(w, h, BLACK)
        elif pattern == 'grid':
            image = draw_grid(w, h, color, step, LINE_WIDTHS[line_idx])
        elif pattern == 'blocks':
            image = draw_blocks(w, h, color, step)
        else:
            image = draw_solid(w, h, color)
        screen.blit(ui.to_surface(image), (0, 0))
        if show_help:
            ui.draw_help(
                screen,
                font,
                [
                    f'pattern: {pattern}   colour: {COLOR_NAMES[color]}   size: {step} px '
                    f'({w // step}x{h // step} squares)   line: {LINE_WIDTHS[line_idx]} px',
                    '1 grid  2 blocks  3 solid  0 black  R/G/B/W C/M/Y/O colour  +/- size  L line',
                    'H help  Q quit',
                ],
            )
        pygame.display.flip()

    show()
    running = True
    while running:
        # batch queued events (key repeat) and redraw once afterwards
        events = [pygame.event.wait()] + pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in ui.QUIT_KEYS:
                    running = False
                elif event.key in PATTERN_KEYS:
                    pattern = PATTERN_KEYS[event.key]
                elif event.key in COLOR_KEYS:
                    color = COLOR_KEYS[event.key]
                elif event.key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS):
                    step_idx = min(step_idx + 1, len(STEPS) - 1)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    step_idx = max(step_idx - 1, 0)
                elif event.key == pygame.K_l:
                    line_idx = (line_idx + 1) % len(LINE_WIDTHS)
                elif event.key == pygame.K_h:
                    show_help = not show_help
        show()
        clock.tick(60)

    if standalone:
        pygame.quit()


def main():
    run()


if __name__ == '__main__':
    main()
