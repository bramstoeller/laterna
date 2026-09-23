"""Shared pygame helpers for the projection apps."""

import os
import pathlib

import pygame

GRAY = (128, 128, 128)
BLACK = (0, 0, 0)
QUIT_KEYS = (pygame.K_q, pygame.K_ESCAPE)

APP_NAME = 'laterna'
# the desktop shows the window under this app id instead of "main.py";
# matches StartupWMClass in laterna.desktop
APP_ID = 'laterna'


def set_canvas(size):
    """The display as a canvas of exactly `size` pixels, filling the whole
    screen: SDL scales it to the desktop, as large as fits with the aspect
    ratio kept (black bars on the other sides). On the projector, whose
    desktop is the canvas size, nothing is scaled: pixel for pixel. The
    desktop's mode never changes, and the mouse is mapped back to canvas
    pixels by SDL."""
    return pygame.display.set_mode(tuple(size), pygame.FULLSCREEN | pygame.SCALED)


def desktop_size():
    """The desktop's size (first display), in pixels."""
    return tuple(pygame.display.get_desktop_sizes()[0])


MENU_WIDTH = 1920  # px across a menu canvas, whatever the screen's resolution


def menu_size():
    """A canvas for the menus: MENU_WIDTH wide, the desktop's aspect ratio,
    so the menus look the same on a projector and a high-resolution laptop."""
    w, h = desktop_size()
    return (MENU_WIDTH, round(MENU_WIDTH * h / w)) if w else (MENU_WIDTH, 1080)


def init_screen(canvas, caption, mouse_visible=False):
    """Fullscreen display with a canvas of `canvas` pixels (see set_canvas;
    None: menu_size()). The mouse is hidden except where it is needed (the
    menu).
    """
    os.environ.setdefault('SDL_VIDEO_X11_WMCLASS', APP_ID)
    os.environ.setdefault('SDL_VIDEO_WAYLAND_WMCLASS', APP_ID)
    # scaling a canvas that is not the desktop's size: smooth, not blocky
    os.environ.setdefault('SDL_RENDER_SCALE_QUALITY', 'linear')
    pygame.init()
    icon = pathlib.Path(__file__).resolve().parent / 'icon' / 'laterna.png'
    if icon.exists():  # rendered by tools/make_icon.py
        pygame.display.set_icon(pygame.image.load(str(icon)))
    screen = set_canvas(menu_size() if canvas is None else canvas)
    pygame.display.set_caption(caption)
    pygame.mouse.set_visible(mouse_visible)
    return screen


def to_surface(image):
    """RGB uint8 numpy image (h, w, 3) -> pygame surface."""
    h, w = image.shape[:2]
    return pygame.image.frombuffer(image.tobytes(), (w, h), 'RGB').convert()


def help_font():
    return pygame.font.SysFont('monospace', 22)


def draw_help(screen, font, lines, top=20):
    for i, line in enumerate(lines):
        screen.blit(font.render(line, True, GRAY, BLACK), (20, top + i * 26))
