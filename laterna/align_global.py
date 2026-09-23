#!/usr/bin/env python
"""Global alignment: the projection's keystone, scale, rotation and position.

Two pages (Tab). Keystone comes first, and only when the projector cannot
stand square on the frames (tilted instead of lens shift): usually there is
none.

keystone: a grid over the whole canvas. Move its four corners until the
  grid lines run parallel to the frames' edges (verticals vertical, rows
  level); that stores `keystone:` in config.yaml, and every render is warped
  through it (render.keystone). All corners at 0 = no keystone.
line: a white horizontal line that should measure exactly 5 m on stage,
  with a 20 cm vertical tick at its centre (both 2 px wide). Adjust until
  the line is level, measures 5 m, and the tick hits the stage centre line —
  then the global calibration in config.yaml is correct and the shapes land
  in mm. The scale keys edit scale_mm_per_px directly: the global transform
  is image_offset (position), rotation, and mm per pixel (scale). At zero
  offset the line sits on the bottom rows of the frame, fully in view.

Keys, keystone page:
  1-4            corner: top left, top right, bottom right, bottom left
  arrow keys     move that corner 1 px   (with Shift: 10 px)
  0              all corners back to 0 (no keystone)
Keys, line page:
  arrow keys     move 1 px   (with Shift: 10 px) — whole pixels, no rounding
  + / -          larger/smaller 0.1%   (with Shift: 1%)
  < / >          rotate ccw/cw 0.02 degrees  (with Shift: 0.2 degrees)
Both:
  Tab            other page
  T              reset to the values at startup
  S              save to config.yaml
  H              help overlay on/off
  Q / ESC        quit (back to the menu)
"""

import argparse

import cv2
import numpy as np
import pygame

from . import calibration, render, ui

LINE_MM = 5000.0
TICK_MM = 200.0
WHITE = (255, 255, 255)
GRID_PX = 120  # keystone grid pitch (plane px)
GRID = (110, 110, 110)
MARK = (255, 200, 0)  # the selected keystone corner
PAGES = ('keystone', 'line')
CORNER_KEYS = {getattr(pygame, f'K_{i + 1}'): i for i in range(4)}
CORNER_KEYS.update({getattr(pygame, f'K_KP_{i + 1}'): i for i in range(4)})


def alignment_image(cfg):
    """Black canvas with the 5 m line and its 20 cm centre tick.

    World y=0 maps to the frame edge (row h), so everything is shifted up
    one pixel: at zero offset the 2 px line covers the bottom two rows,
    fully in view.
    """
    w, h = cfg['canvas']
    canvas = np.zeros((h, w, 3), np.uint8)
    pts = render.world_to_px(
        np.array(
            [
                [-LINE_MM / 2, 0.0],
                [LINE_MM / 2, 0.0],  # line
                [0.0, 0.0],
                [0.0, TICK_MM],  # centre tick
            ]
        ),
        cfg,
    )
    pts[:, 1] -= 1.0
    p = [tuple(q) for q in np.round(pts).astype(int)]
    cv2.line(canvas, p[0], p[1], WHITE, 2, cv2.LINE_AA)
    cv2.line(canvas, p[2], p[3], WHITE, 2, cv2.LINE_AA)
    return canvas


def keystone_image(cfg, corner):
    """The keystone page as the projector shows it: a grid over the plane
    canvas with its border, the 5 m line on top, warped through the
    keystone; a ring on the selected corner."""
    w, h = cfg['canvas']
    canvas = alignment_image(cfg)
    for x in range(0, w + 1, GRID_PX):
        cv2.line(canvas, (min(x, w - 1), 0), (min(x, w - 1), h - 1), GRID, 1)
    for y in range(0, h + 1, GRID_PX):
        cv2.line(canvas, (0, min(y, h - 1)), (w - 1, min(y, h - 1)), GRID, 1)
    cv2.rectangle(canvas, (0, 0), (w - 1, h - 1), WHITE, 2)
    out = np.ascontiguousarray(render.keystone_image(canvas, cfg))
    src = np.float64([[0, 0], [w, 0], [w, h], [0, h]])
    x, y = src[corner] + np.float64(calibration.keystone_offsets(cfg)[corner])
    cv2.circle(out, (int(round(x)), int(round(y))), 40, MARK, 3, cv2.LINE_AA)
    return out


def self_test(cfg):
    """Headless check of the render and edit functions; does not save."""
    snap = calibration.snapshot(cfg)
    render.save_png(alignment_image(cfg), 'renders/align_global.png')
    calibration.move_keystone(cfg, 0, 30, 20)
    calibration.move_keystone(cfg, 2, -15, 0)
    render.save_png(keystone_image(cfg, 0), 'renders/align_keystone.png')
    calibration.move(cfg, None, 50, -25)
    calibration.scale_by(cfg, None, 0.01)
    calibration.rotate_by(cfg, None, 0.5)
    calibration.reset(cfg, None, snap)
    assert cfg['image_offset'] == snap[None][0]
    assert cfg['scale_mm_per_px'] == snap[None][1]
    assert cfg['rotation'] == snap[None][2]
    assert calibration.keystone_offsets(cfg) == snap['keystone']
    print('self-test ok (written: renders/align_global.png, renders/align_keystone.png)')


def run(screen=None, config='config.yaml'):
    """Run the app; with a screen provided, reuse it (menu mode)."""
    cfg = render.load_config(config)
    snap = calibration.snapshot(cfg)
    dirty = False
    show_help = True
    message = ''
    page = 0  # keystone first, then the line (Tab)
    corner = 0

    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Global alignment — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Global alignment — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    font = ui.help_font()
    clock = pygame.time.Clock()
    pygame.key.set_repeat(350, 40)  # hold a key to keep stepping

    def show():
        if PAGES[page] == 'keystone':
            image = keystone_image(cfg, corner)
        else:
            image = render.keystone_image(alignment_image(cfg), cfg)
        screen.blit(ui.to_surface(image), (0, 0))
        if show_help:
            offset = cfg.get('image_offset', [0.0, 0.0])
            mmpp = float(cfg['scale_mm_per_px'])
            head = f'[{PAGES[page]}]  page {page + 1}/{len(PAGES)} (Tab)' + (
                '   * unsaved changes *' if dirty else ''
            )
            if PAGES[page] == 'keystone':
                offsets = calibration.keystone_offsets(cfg)
                lines = [
                    head,
                    'corners (px): '
                    + '   '.join(
                        f'{"*" if i == corner else ""}{i + 1} {name} ({dx:+.0f}, {dy:+.0f})'
                        for i, (name, (dx, dy)) in enumerate(zip(render.KEYSTONE_CORNERS, offsets))
                    ),
                    'grid lines parallel to the frames; usually no keystone (lens shift)',
                    '1-4 corner  arrows move 1 px (Shift 10)  0 no keystone',
                    'T reset  S save  H help  Q quit',
                ]
            else:
                lines = [
                    head,
                    f'line: {LINE_MM / 1000:.1f} m wide, tick {TICK_MM / 10:.0f} cm',
                    f'translate: ({offset[0] / mmpp:+.1f}, {offset[1] / mmpp:+.1f}) px   '
                    f'scale: {mmpp:.4f} mm/px ({LINE_MM / mmpp:.0f} px)   '
                    f'rotation: {float(cfg.get("rotation", 0.0)):+.2f} deg',
                    'arrows move 1 px  +/- scale  < > rotate  (Shift = coarse)',
                    'T reset  S save  H help  Q quit',
                ]
            if message:
                lines.append(message)
            ui.draw_help(screen, font, lines)
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
                shift = event.mod & pygame.KMOD_SHIFT
                # translation in whole pixels, converted to mm at the current
                # scale — exact, no rounding
                mm = (10 if shift else 1) * float(cfg['scale_mm_per_px'])
                d_scale = 0.01 if shift else 0.001
                d_angle = 0.2 if shift else 0.02
                message = ''
                keystone_page = PAGES[page] == 'keystone'
                step = 10 if shift else 1
                if event.key in ui.QUIT_KEYS:
                    running = False
                elif event.key == pygame.K_TAB:
                    page = (page + 1) % len(PAGES)
                elif keystone_page and event.key in CORNER_KEYS:
                    corner = CORNER_KEYS[event.key]
                elif keystone_page and event.key in (pygame.K_0, pygame.K_KP_0):
                    calibration.set_keystone(cfg, [[0.0, 0.0]] * 4)
                    dirty = True
                    message = 'no keystone'
                elif keystone_page and event.key in (
                    pygame.K_LEFT,
                    pygame.K_RIGHT,
                    pygame.K_UP,
                    pygame.K_DOWN,
                ):
                    dx = {pygame.K_LEFT: -step, pygame.K_RIGHT: step}.get(event.key, 0)
                    dy = {pygame.K_UP: -step, pygame.K_DOWN: step}.get(event.key, 0)
                    calibration.move_keystone(cfg, corner, dx, dy)
                    dirty = True
                elif event.key == pygame.K_LEFT:
                    calibration.move(cfg, None, -mm, 0)
                    dirty = True
                elif event.key == pygame.K_RIGHT:
                    calibration.move(cfg, None, mm, 0)
                    dirty = True
                elif event.key == pygame.K_UP:
                    calibration.move(cfg, None, 0, mm)
                    dirty = True
                elif event.key == pygame.K_DOWN:
                    calibration.move(cfg, None, 0, -mm)
                    dirty = True
                elif event.key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS):
                    calibration.scale_by(cfg, None, d_scale)
                    dirty = True
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    calibration.scale_by(cfg, None, -d_scale)
                    dirty = True
                # positive rotation is counterclockwise on the projection,
                # so < turns left and > turns right
                elif event.key == pygame.K_COMMA:
                    calibration.rotate_by(cfg, None, d_angle)
                    dirty = True
                elif event.key == pygame.K_PERIOD:
                    calibration.rotate_by(cfg, None, -d_angle)
                    dirty = True
                elif event.key == pygame.K_t:
                    calibration.reset(cfg, None, snap)
                    dirty = True
                    message = 'global calibration reset to startup values'
                elif event.key == pygame.K_s:
                    calibration.save_config(cfg, config)
                    dirty = False
                    message = f'saved to {config}'
                elif event.key == pygame.K_h:
                    show_help = not show_help
        show()
        clock.tick(60)

    if standalone:
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description='Global scale/rotation alignment')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument(
        '--test', action='store_true', help='self-test: render one alignment image, no display'
    )
    args = ap.parse_args()
    if args.test:
        self_test(render.load_config(args.config))
        return
    run(config=args.config)


if __name__ == '__main__':
    main()
