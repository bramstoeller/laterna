"""On-screen faders for the light desk's channels, a hidden feature of
Present (Tab). One fader per channel offset of the fixture (laterna/dmx.py),
across the top of the picture: the knob is the value the lamps are on
(after the smoothing), the thin line is what the desk sends (drawn once
a desk has sent anything). Dragging a
fader takes that channel over by hand; it holds until the desk moves the
channel itself (latest takes precedence, as on a desk). The wheel steps
by one. A fader can also be selected (select(), shown with a marker) and
nudged (nudge()) from the keyboard. Without a desk (dmx.source off) the
faders are the only source.
"""

import pygame

PANEL = (0, 0, 0, 170)
TRACK = (60, 56, 50)
FILL = (200, 150, 60)
HAND = (90, 190, 230)  # a channel taken over by hand
TARGET = (240, 240, 240)  # what the desk sends
TEXT = (200, 190, 170)
DIM = (130, 120, 105)
SELECTED = (255, 240, 200)

COLUMN = 72
TRACK_H = 150
TRACK_W = 14
MARGIN = 20


class Faders:
    def __init__(self, desk, font):
        self.desk = desk
        self.font = font
        self.visible = False
        self.drag = None
        self.selected = None  # column index chosen from the keyboard
        self.columns = []  # (offset, track rect)
        x = MARGIN
        for offset in desk.offsets:
            self.columns.append(
                (offset, pygame.Rect(x + (COLUMN - TRACK_W) // 2, MARGIN + 24, TRACK_W, TRACK_H))
            )
            x += COLUMN
        self.height = MARGIN + 24 + TRACK_H + 8 + 2 * 18 + MARGIN
        self.width = x + MARGIN - COLUMN // 2 + COLUMN // 2

    def toggle(self):
        self.visible = not self.visible
        self.drag = None
        pygame.mouse.set_visible(self.visible)

    def _at(self, pos):
        """The fader column under the mouse (track plus a margin), or None."""
        for offset, track in self.columns:
            if track.inflate(COLUMN - TRACK_W, 24).collidepoint(pos):
                return offset, track
        return None

    def _set(self, offset, track, y):
        value = 255.0 * (1.0 - (y - track.top) / track.height)
        self.desk.set_override(offset, value)

    def select(self, index):
        """Choose column `index` for nudge(); False when there is none."""
        if not 0 <= index < len(self.columns):
            return False
        self.selected = index
        return True

    def nudge(self, steps):
        """Move the selected fader by `steps` values (by hand)."""
        if self.selected is None:
            return
        offset, _ = self.columns[self.selected]
        self.desk.set_override(offset, self.desk.frame()[offset] + steps)

    def label(self, index):
        offset, _ = self.columns[index]
        return ' '.join(part for part in self.desk.offset_labels[offset] if part)

    def handle(self, event):
        """Mouse on the faders; True when the event was for them."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            hit = self._at(event.pos)
            if hit:
                self.drag = hit
                self._set(*hit, event.pos[1])
                return True
        elif event.type == pygame.MOUSEMOTION and self.drag:
            if event.buttons[0]:
                self._set(*self.drag, event.pos[1])
            else:
                self.drag = None
            return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.drag:
            self.drag = None
            return True
        elif event.type == pygame.MOUSEWHEEL:
            hit = self._at(pygame.mouse.get_pos())
            if hit:
                offset, _ = hit
                self.desk.set_override(offset, self.desk.frame()[offset] + event.y)
                return True
        return False

    def draw(self, screen):
        """Draw the panel; returns its height (for what goes below it)."""
        values = self.desk.frame()
        targets = self.desk.targets() if self.desk.receiving else None
        panel = pygame.Surface((screen.get_width(), self.height), pygame.SRCALPHA)
        panel.fill(PANEL)
        screen.blit(panel, (0, 0))
        for index, (offset, track) in enumerate(self.columns):
            value = values[offset]
            by_hand = offset in self.desk.override
            if index == self.selected:
                pygame.draw.rect(screen, SELECTED, track.inflate(14, 14), 1, border_radius=8)
            pygame.draw.rect(screen, TRACK, track, border_radius=4)
            top = track.bottom - int(round(track.height * value / 255.0))
            if top < track.bottom:
                pygame.draw.rect(
                    screen,
                    HAND if by_hand else FILL,
                    pygame.Rect(track.left, top, track.width, track.bottom - top),
                    border_radius=4,
                )
            if targets is not None:
                ty = track.bottom - int(round(track.height * targets[offset] / 255.0))
                pygame.draw.line(screen, TARGET, (track.left - 6, ty), (track.right + 6, ty), 2)
            centre = track.centerx
            head = self.font.render(f'{value:.0f}', True, TEXT)
            screen.blit(head, (centre - head.get_width() // 2, track.top - 22))
            for k, line in enumerate(self.desk.offset_labels[offset]):
                text = self.font.render(line, True, DIM)
                screen.blit(text, (centre - text.get_width() // 2, track.bottom + 8 + k * 18))
        return self.height
