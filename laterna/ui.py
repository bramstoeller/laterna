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


def init_screen(canvas, caption, mouse_visible=False):
    """Fullscreen display at exactly the canvas size.

    Everything runs fullscreen; the projector desktop must be exactly the
    canvas size from config.yaml (the projector's native pixels). The mouse
    is hidden except where it is needed (the menu).
    """
    os.environ.setdefault('SDL_VIDEO_X11_WMCLASS', APP_ID)
    os.environ.setdefault('SDL_VIDEO_WAYLAND_WMCLASS', APP_ID)
    pygame.init()
    icon = pathlib.Path(__file__).resolve().parent / 'icon' / 'laterna.png'
    if icon.exists():  # rendered by tools/make_icon.py
        pygame.display.set_icon(pygame.image.load(str(icon)))
    screen = pygame.display.set_mode(tuple(canvas), pygame.FULLSCREEN)
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
