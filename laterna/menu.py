"""laterna: projection onto stage frames. First pick a show, then its apps.

A show is a folder with a config.yaml (and its scenes.yaml, images/,
videos/); every config.yaml below the working directory (or the folder
given on the command line; frozen: the executable's folder) is offered,
e.g. shows/example/config.yaml. The chosen show's
folder becomes the working directory, so each show keeps its own cache/,
renders/ and export/.

The apps, in show order:

  1  test pattern      (static pixel patterns)
  2  dynamic range     (near-black/white steps, staircase, gamma chart)
  3  global alignment  (scale, rotation, position -> config.yaml)
  4  shape calibration (align objects with the frames -> config.yaml)
  5  look              (brightness per layer, spotlights -> config.yaml)
  6  export            (config, run sheet and backup as PDF -> export/)
  7  present           (play the stages from scenes.yaml)

Click a button or press its number. The apps run in this process and reuse
the menu's fullscreen display, so there is no mode switch (no flicker);
Q / ESC in an app returns to the apps, there to the shows, there quits.
"""

import os
import pathlib
import sys

import pygame
import yaml

from . import align_global, configure, dynamic_range, export, look, play, render, test_pattern, ui

# folders never searched for shows
SKIP_DIRS = {
    'venv',
    '.venv',
    '.git',
    '_internal',
    '__pycache__',
    'cache',
    'renders',
    'export',
    'images',
    'videos',
    '_archive',
    'build',
    'dist',
    'node_modules',
}
APPS = [
    ('Test pattern', test_pattern),
    ('Dynamic range', dynamic_range),
    ('Global alignment', align_global),
    ('Shape calibration', configure),
    ('Look', look),
    ('Export', export),
    ('Present', play),
]
BACKGROUND = (25, 22, 18)
BUTTON = (55, 46, 32)
BUTTON_HOVER = (85, 70, 45)
TEXT = (230, 210, 170)
SUBTEXT = (150, 135, 110)
MAX_ITEMS = 9  # number keys 1-9


def find_shows(root):
    """The folders below `root` holding a config.yaml, sorted by path."""
    shows = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        if 'config.yaml' in files and pathlib.Path(folder) != root:
            shows.append(pathlib.Path(folder))
    return shows


def show_description(folder):
    """The optional `description` at the top of a show's config.yaml."""
    try:
        with open(folder / 'config.yaml') as f:
            return str((yaml.safe_load(f) or {}).get('description') or '').strip()
    except Exception as exc:
        return f'unreadable: {exc}'


def menu(screen, title, subtitle, items, on_pick, quit_hint='Q goes back'):
    """Buttons named `items`; on_pick(index) runs the
    choice and returns a message for the footer ('' for none), or None to
    leave the menu. Returns when Q / ESC is pressed or on_pick says so."""
    width, height = screen.get_size()
    items = items[:MAX_ITEMS]
    button_width = min(1100, width - 80)
    x = (width - button_width) // 2
    # the buttons share the height between the title and the footer
    pitch = min(90, (height - 240 - 170) // max(len(items), 1))
    title_font = pygame.font.SysFont('monospace', 56, bold=True)
    font = pygame.font.SysFont('monospace', min(40, pitch // 2))
    small = pygame.font.SysFont('monospace', 26)
    buttons = [pygame.Rect(x, 240 + i * pitch, button_width, pitch - 17) for i in range(len(items))]
    message = ''

    def draw():
        screen = pygame.display.get_surface()  # an app may have set a new mode
        screen.fill(BACKGROUND)
        text = title_font.render(title, True, TEXT)
        screen.blit(text, ((width - text.get_width()) // 2, 110))
        text = small.render(subtitle, True, SUBTEXT)
        if text.get_width() > width - 40:  # a long path: keep its end
            text = text.subsurface(
                (text.get_width() - (width - 40), 0, width - 40, text.get_height())
            )
        screen.blit(text, ((width - text.get_width()) // 2, 180))
        mouse = pygame.mouse.get_pos()
        for i, (rect, name) in enumerate(zip(buttons, items)):
            hover = rect.collidepoint(mouse)
            pygame.draw.rect(screen, BUTTON_HOVER if hover else BUTTON, rect, border_radius=12)
            text = font.render(f'{i + 1}  {name}', True, TEXT)
            screen.blit(text, (rect.x + 40, rect.centery - text.get_height() // 2))
        hint = f'click or press 1-{len(items)} — {quit_hint}' if items else quit_hint
        lines = (message or hint).splitlines()[:4]
        for k, line in enumerate(lines):
            text = small.render(line, True, SUBTEXT)
            screen.blit(
                text, ((width - text.get_width()) // 2, height - 60 - 32 * (len(lines) - k))
            )
        pygame.display.flip()

    def pick(index):
        nonlocal message
        result = on_pick(index)
        pygame.mouse.set_visible(True)
        pygame.key.set_repeat()  # apps may enable key repeat; the menu wants none
        if result is None:
            return False
        message = result
        return True

    number_keys = {}
    for i in range(len(items)):
        number_keys[getattr(pygame, f'K_{i + 1}')] = i
        number_keys[getattr(pygame, f'K_KP_{i + 1}')] = i

    clock = pygame.time.Clock()
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                raise SystemExit
            if event.type == pygame.KEYDOWN:
                if event.key in ui.QUIT_KEYS:
                    return
                if event.key in number_keys and not pick(number_keys[event.key]):
                    return
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, rect in enumerate(buttons):
                    if rect.collidepoint(event.pos):
                        if not pick(i):
                            return
                        break
        draw()
        clock.tick(30)


def app_menu(screen, folder):
    """The apps for the show in `folder` (the working directory by now)."""
    caption = f'{ui.APP_NAME} — {folder.name}'

    def launch(index):
        module = APPS[index][1]
        try:
            module.run(screen)
            message = ''
        except Exception as exc:
            message = f'{module.__name__}: {exc}'
        pygame.display.set_caption(caption)
        return message

    pygame.display.set_caption(caption)
    menu(screen, folder.name, show_description(folder), [name for name, _ in APPS], launch)


def root_folder():
    """Where the shows are searched: the folder given on the command line,
    else next to the executable (frozen, PyInstaller), else the working
    directory."""
    if len(sys.argv) > 1:
        return pathlib.Path(sys.argv[1]).resolve()
    if getattr(sys, 'frozen', False):
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path.cwd()


def main():
    root = root_folder()
    shows = find_shows(root)
    # the picker opens at the desktop's size; a show then needs the display
    # at exactly its canvas (on site the projector desktop already is)
    screen = ui.init_screen((0, 0), ui.APP_NAME, mouse_visible=True)
    picker_size = screen.get_size()

    def open_show(index):
        nonlocal screen
        folder = shows[index]
        try:
            cfg = render.load_config(folder / 'config.yaml')
        except Exception as exc:
            return f'{folder.name}: {exc}'
        canvas = tuple(cfg['canvas'])
        if screen.get_size() != canvas:
            screen = pygame.display.set_mode(canvas, pygame.FULLSCREEN)
        os.chdir(folder)  # config.yaml, scenes.yaml, media and caches resolve here
        try:
            app_menu(screen, folder)
        finally:
            os.chdir(root)
        if screen.get_size() != picker_size:
            screen = pygame.display.set_mode(picker_size, pygame.FULLSCREEN)
        pygame.display.set_caption(ui.APP_NAME)
        return ''

    if not shows:
        subtitle = f'no config.yaml found in the folders below {root}'
    else:
        subtitle = 'choose a show'
    menu(screen, ui.APP_NAME, subtitle, [f.name for f in shows], open_show, quit_hint='Q quits')
    pygame.quit()
