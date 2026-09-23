#!/usr/bin/env python
"""Shape calibration: match the projected objects with the physical frames.

Projects the selected object with a green molding and a red screen;
non-selected objects are all blue. Adjusts translation, scale and rotation
per object, and then the individual corners of the frame (frame.py); the
global calibration (scale/rotation/position) is done in align_global.py
first.

Two pages, Tab cycles them:

  placement  the selected object with a green molding and a red screen,
             the others all blue (the filled render, as projected)
  corners    a wireframe of 1 px lines: the projected contour (the circle),
             the inner edge of the wood and the joints between the planks;
             the selected object white, the others grey, and a green cross
             on the selected corner

Selection:
  1..4       select an object
  Tab        next page
  < / >      corners page: previous/next corner

Editing (movement is in world mm):
  arrow keys     placement: move the object 1 mm; corners: move the
                 selected corner 1 mm  (Shift: 10 mm)
  + / -          larger/smaller 1 mm in the largest dimension  (Shift: 10 mm)
  < / >          placement: rotate ccw/cw 0.02 degrees  (Shift: 0.2 degrees)
  T              placement: reset the object; corners: reset the selected corner
  Shift+T        corners: reset all corners of the object
  S              save to config.yaml

A corner is an inner corner of the wood, where it meets the cloth: the
bottom corners and the knuckles of the arch, from bottom left over the top
to bottom right. The wood's outer contour, the molding and its fillets
follow from the plank width (frame.py). The corners are the object's
`frame.inner` in local mm, so they survive a later re-alignment of the
object or the whole image.

Other:
  H  help overlay on/off
  Q / ESC  quit (back to the menu when started from main.py)
"""

import argparse

import cv2
import numpy as np
import pygame

from . import calibration, render, ui

GREEN = (0, 255, 0)
BLUE = (0, 0, 200)
RED = (200, 0, 0)
WHITE = (255, 255, 255)
GREY = (90, 90, 90)

SELECT_KEYS = {
    pygame.K_1: 1,
    pygame.K_KP_1: 1,
    pygame.K_2: 2,
    pygame.K_KP_2: 2,
    pygame.K_3: 3,
    pygame.K_KP_3: 3,
    pygame.K_4: 4,
    pygame.K_KP_4: 4,
}
SHIFT = 4  # sub-pixel precision of the line drawing (1/16 px)
CROSS = 12  # half size of the corner cross, px
PAGES = ('placement', 'corners')


def _line(canvas, points, color, closed):
    pts = np.round(np.asarray(points, np.float64) * (1 << SHIFT)).astype(np.int32)
    cv2.polylines(canvas, [pts], closed, color, 1, cv2.LINE_AA, shift=SHIFT)


def _cross(canvas, point, color, half):
    x, y = np.asarray(point, np.float64) * (1 << SHIFT)
    for dx, dy in ((half, 0), (0, half)):
        a = (int(round(x - dx * (1 << SHIFT))), int(round(y - dy * (1 << SHIFT))))
        b = (int(round(x + dx * (1 << SHIFT))), int(round(y + dy * (1 << SHIFT))))
        cv2.line(canvas, a, b, color, 1, cv2.LINE_AA, shift=SHIFT)


def wireframe_lines(cfg, obj):
    """The wireframe of an object in canvas px: the projected outer contour
    (the circle), the inner edge of the wood (the picture area) and the
    joints between the planks (inner corner to outer corner, the mitres).
    [(points, closed)]"""
    lines = [
        (render.world_to_px(poly, cfg), True) for poly in render.object_polygons_world(cfg, obj)
    ]
    lines += [
        (render.world_to_px(poly, cfg), True) for poly in render.object_canvas_world(cfg, obj)
    ]
    inner = render.world_to_px(render.object_corners_world(cfg, obj), cfg)
    outer = render.world_to_px(render.object_wood_world(cfg, obj), cfg)
    lines += [(np.array([a, b]), False) for a, b in zip(inner, outer)]
    return lines


def calibration_image(cfg, selected, corner=None):
    """Placement page (corner None): the selected object with a green
    molding and a red screen, the others all blue. Corners page: a
    wireframe, white for the selected object and grey for the others, with
    a green cross on the selected corner."""
    w, h = cfg['canvas']
    canvas = np.zeros((h, w, 3), np.uint8)
    for obj in cfg['objects']:
        chosen = obj['id'] == selected
        polys = render.polys_px(cfg, [obj['id']])
        if corner is None:
            inside, molding, _, _, _ = render.compute_molding(
                (h, w),
                polys,
                render.strokes_px(cfg, [obj['id']]),
                cfg,
                ss=1,
                canvas_px=render.canvas_px(cfg, [obj['id']]),
            )
            canvas[inside] = RED if chosen else BLUE
            canvas[molding] = GREEN if chosen else BLUE
        else:
            for pts, closed in wireframe_lines(cfg, obj):
                _line(canvas, pts, WHITE if chosen else GREY, closed)
        # the number in the middle of the object's bounding box (the mean
        # of the vertices sat too high: the arc has most of them)
        points = np.vstack(polys)
        cx, cy = ((points.min(axis=0) + points.max(axis=0)) / 2.0).astype(int)
        label = str(obj['id'])
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 1.6, 2)
        cv2.putText(
            canvas,
            label,
            (cx - tw // 2, cy + th // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.6,
            WHITE,
            2,
            cv2.LINE_AA,
        )
        if chosen and corner is not None:
            corners = render.world_to_px(render.object_corners_world(cfg, obj), cfg)
            _cross(canvas, corners[corner], GREEN, CROSS)
    return canvas


def self_test(cfg):
    """Headless check of the render and edit functions; does not save."""
    snap = calibration.snapshot(cfg)
    corner_snap = calibration.corner_snapshot(cfg)
    render.save_png(calibration_image(cfg, selected=2), 'renders/configure.png')
    calibration.move(cfg, 2, 50, -25)
    calibration.scale_by(cfg, 2, 1.0)
    calibration.rotate_by(cfg, 2, 0.5)
    calibration.reset(cfg, 2, snap)
    obj = calibration.get_object(cfg, 2)
    assert obj['origin'] == snap[2][0]
    assert obj['scale'] == snap[2][1]
    assert obj['rotation'] == snap[2][2]
    before = render.object_corners_world(cfg, obj).copy()
    calibration.move_corner(cfg, 2, 1, 10.0, 0.0)
    after = render.object_corners_world(cfg, obj)
    assert np.allclose(after[1] - before[1], (10.0, 0.0), atol=0.1), after[1] - before[1]
    assert np.allclose(after[0], before[0])
    render.save_png(calibration_image(cfg, selected=2, corner=1), 'renders/configure-wireframe.png')
    calibration.reset_corners(cfg, 2, corner_snap)
    assert obj['frame']['inner'] == corner_snap[2]
    print('self-test ok (written: renders/configure.png, renders/configure-wireframe.png)')


def run(screen=None, config='config.yaml'):
    """Run the app; with a screen provided, reuse it (menu mode)."""
    cfg = render.load_config(config)
    snap = calibration.snapshot(cfg)
    corner_snap = calibration.corner_snapshot(cfg)
    selected = 1
    page = 0  # index into PAGES
    corner = 0  # the selected corner on the corners page
    dirty = False
    show_help = True
    message = ''

    standalone = screen is None
    if standalone:
        screen = ui.init_screen(cfg['canvas'], f'Shape calibration — {ui.APP_NAME}')
    else:
        pygame.display.set_caption(f'Shape calibration — {ui.APP_NAME}')
        pygame.mouse.set_visible(False)
    font = ui.help_font()
    clock = pygame.time.Clock()
    pygame.key.set_repeat(350, 40)  # hold a key to keep stepping

    def on_corners():
        return PAGES[page] == 'corners'

    def show():
        screen.blit(
            ui.to_surface(
                render.keystone_image(
                    calibration_image(cfg, selected, corner if on_corners() else None), cfg
                )
            ),
            (0, 0),
        )
        if show_help:
            obj = calibration.get_object(cfg, selected)
            origin = obj['origin']
            size = calibration.object_size_mm(cfg, obj) * float(obj.get('scale', 1.0))
            n = calibration.corner_count(cfg, obj)
            lines = [
                f'[{PAGES[page]}]  page {page + 1}/{len(PAGES)} (Tab)   '
                f'object {selected} ({obj["name"]})' + ('   * unsaved changes *' if dirty else '')
            ]
            if on_corners():
                x, y = obj['frame']['inner'][corner]
                lines += [
                    f'corner {corner + 1}/{n}   at ({x:+.1f}, {y:+.1f}) local mm',
                    '1-4 select object   < > previous/next corner',
                    'arrows move corner 1 mm  (Shift = x10)',
                    'T reset corner  Shift+T reset all corners  S save  H help  Q quit',
                ]
            else:
                lines += [
                    f'origin: ({origin[0]:+.1f}, {origin[1]:+.1f}) mm   '
                    f'size: {size:.1f} mm   '
                    f'rotation: {float(obj.get("rotation", 0.0)):+.2f} deg',
                    '1-4 select object',
                    'arrows move 1 mm  +/- size 1 mm  < > rotate 0.02 deg  (Shift = x10)',
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
                mm = 10.0 if shift else 1.0
                d_size = 10.0 if shift else 1.0
                d_angle = 0.2 if shift else 0.02
                n = calibration.corner_count(cfg, calibration.get_object(cfg, selected))
                message = ''
                if event.key in ui.QUIT_KEYS:
                    running = False
                elif event.key in SELECT_KEYS:
                    wanted = SELECT_KEYS[event.key]
                    if any(o['id'] == wanted for o in cfg['objects']):
                        selected = wanted
                        corner = 0
                elif event.key == pygame.K_TAB:
                    page = (page + 1) % len(PAGES)
                elif event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
                    dx, dy = {
                        pygame.K_LEFT: (-mm, 0),
                        pygame.K_RIGHT: (mm, 0),
                        pygame.K_UP: (0, mm),
                        pygame.K_DOWN: (0, -mm),
                    }[event.key]
                    if on_corners():
                        calibration.move_corner(cfg, selected, corner, dx, dy)
                    else:
                        calibration.move(cfg, selected, dx, dy)
                    dirty = True
                elif event.key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS):
                    calibration.scale_by(cfg, selected, d_size)
                    dirty = True
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    calibration.scale_by(cfg, selected, -d_size)
                    dirty = True
                # on the corners page < > walk the corners; on the placement
                # page they rotate: positive rotation is counterclockwise on
                # the projection, so < turns left and > turns right
                elif event.key == pygame.K_COMMA:
                    if on_corners():
                        corner = (corner - 1) % n
                    else:
                        calibration.rotate_by(cfg, selected, d_angle)
                        dirty = True
                elif event.key == pygame.K_PERIOD:
                    if on_corners():
                        corner = (corner + 1) % n
                    else:
                        calibration.rotate_by(cfg, selected, -d_angle)
                        dirty = True
                elif event.key == pygame.K_t:
                    if on_corners() and shift:
                        calibration.reset_corners(cfg, selected, corner_snap)
                        message = 'all corners reset to startup values'
                    elif on_corners():
                        calibration.reset_corners(cfg, selected, corner_snap, corner)
                        message = f'corner {corner + 1} reset to startup values'
                    else:
                        calibration.reset(cfg, selected, snap)
                        message = 'object reset to startup values'
                    dirty = True
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
    ap = argparse.ArgumentParser(description='Align the objects with the frames')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument(
        '--test', action='store_true', help='self-test: render one calibration image, no display'
    )
    args = ap.parse_args()
    if args.test:
        self_test(render.load_config(args.config))
        return
    run(config=args.config)


if __name__ == '__main__':
    main()
