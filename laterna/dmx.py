#!/usr/bin/env python
"""DMX input: the light desk dims the projection's pretend lamps.

The desk sees the projection as one fixture at `address` (config.yaml
`dmx:`), whose channel map is `channels:` (offsets 1.. from the address):

  master        everything (0 = black)
  cct           colour temperature of the light at full, for every lamp:
                128 = look.temperature, 0..128 = from `cct[0]` (warm) up
                to it, 128..255 = from it to `cct[1]` (cool), each half
                linear in mired. The projector's white is 6500 K: above
                that the light goes bluish, below it warm. Dimming reddens
                from there like a tungsten filament (laterna/lamps.py)
  per object (by id or name):
    canvas      the lamp on its picture (image, video or fill colour)
    frame       the lamp on its molding (`molding` is accepted too)
    power       optional: both lamps of the object together, on top of
                canvas and frame

Without `channels:` the map is master 1, cct 2, then per object in
config order canvas, frame (8 channels for three frames). A missing
channel is simply not used (no master = always full, no cct = as
rendered).
Levels are a dimmer's: linear in display values (128 = half the pixel
value). See laterna/lamps.py for the compositing.

Sources (`dmx.source`):
  sacn    streaming ACN (E1.31) over the network, ETC Eos' native output;
          `universe` 1..63999 as the desk shows it
  artnet  Art-Net over the network; `universe` = the 15-bit port address
          (net x 256 + subnet x 16 + universe), 0-based as Eos shows it
  enttec  an Enttec DMX USB Pro, or anything speaking its widget protocol
          (DMXking, a DIY widget), on `port` (a serial device, or `auto`)
  demo    no desk, no hardware: a scripted desk inside (DEMO below) that
          fades, snaps, blacks out, sweeps the colour temperature and
          chases the pictures, 40 frames a second like a real one, on a
          loop. `python -m laterna.play --dmx demo` shows what the lamps do
  off     no desk: the show plays at full light (the default)

Smooth dimming: the desk sends 8-bit values 40 times a second and we
draw 60; a fast fader or a snap cue arrives in steps of several values a
frame, visible as jolts, worst in the dark. So every channel in use is
low-pass filtered: each tick it moves a fraction of the way to what the
desk sends, a' = a + (1 - exp(-dt / tau)) x (b - a), with `smooth: [up,
down]` the time constants in seconds (one number = both; `false` = off).
The default [0.04, 0.08] is a tungsten filament's: it heats faster than
it cools, so a snap up is at 90 % after 0.1 s and done in 0.25 s, a snap
to black is visually black after 0.3 s with the glow gone by 0.5 s, and
the last steps into black are small because each step is a fraction of
what is left. The colour temperature is filtered along (up = cooler),
so it never jumps either.

Until the desk's first frame the levels are full, so the show also runs
with nothing connected. After that a channel stays full too (cct: 128)
until its value changes: whatever a desk sends at first (0 for a
channel it has not patched, or what it booted with) does not black the
show out; once the channel moves the desk rules it (faded to from
full). When
the signal stops the last frame holds (a DMX receiver never blacks out
by itself).
`python -m laterna.dmx` prints the fixture's channels live: the on-site check
that the cable and the patch are right.
"""

import argparse
import math
import socket
import struct
import sys
import threading
import time

DEFAULTS = {
    'source': 'off',
    'universe': 1,
    'port': 'auto',
    'address': 1,
    'cct': [2250.0, 6500.0],
    'channels': None,
    'smooth': [0.04, 0.08],
}
SOURCES = ('off', 'sacn', 'artnet', 'enttec', 'demo')
GLOBAL_CHANNELS = ('master', 'cct')
OBJECT_CHANNELS = ('canvas', 'frame', 'power')
ALIASES = {'molding': 'frame'}
SACN_PORT = 5568
ARTNET_PORT = 6454
ACN_ID = b'ASC-E1.17\x00\x00\x00'


def default_channels(cfg):
    """master, cct, then canvas and frame per object."""
    channels = {'master': 1, 'cct': 2, 'objects': {}}
    for k, o in enumerate(cfg['objects']):
        channels['objects'][o['id']] = {'canvas': 3 + 2 * k, 'frame': 4 + 2 * k}
    return channels


def _offset(value, what):
    try:
        offset = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'dmx.channels.{what}: a channel offset is a number from 1') from None
    if offset < 1:
        raise ValueError(f'dmx.channels.{what}: a channel offset is a number from 1')
    return offset


def parse_channels(value, cfg):
    """The channel map as {'master': offset|None, 'cct': offset|None,
    'objects': {id: {name: offset}}} with every object present."""
    if not isinstance(value, dict) or not set(value) <= set(GLOBAL_CHANNELS) | {'objects'}:
        raise ValueError(f'dmx.channels has {", ".join(GLOBAL_CHANNELS)} and objects')
    channels = {
        name: _offset(value[name], name) if value.get(name) is not None else None
        for name in GLOBAL_CHANNELS
    }
    by_key = {}
    for o in cfg['objects']:
        by_key[o['id']] = o['id']
        if 'name' in o:
            by_key[o['name']] = o['id']
    channels['objects'] = {o['id']: {} for o in cfg['objects']}
    for key, spec in (value.get('objects') or {}).items():
        if key not in by_key:
            raise ValueError(f'dmx.channels.objects: unknown object {key}')
        if isinstance(spec, dict):
            spec = {ALIASES.get(name, name): value for name, value in spec.items()}
        if not isinstance(spec, dict) or not set(spec) <= set(OBJECT_CHANNELS):
            raise ValueError(f'dmx.channels.objects.{key}: {", ".join(OBJECT_CHANNELS)}')
        channels['objects'][by_key[key]] = {
            name: _offset(spec[name], f'objects.{key}.{name}')
            for name in OBJECT_CHANNELS
            if spec.get(name) is not None
        }
    return channels


def channel_span(channels):
    """The highest offset in use (0 for none)."""
    offsets = [channels[n] for n in GLOBAL_CHANNELS if channels[n]]
    offsets += [off for spec in channels['objects'].values() for off in spec.values()]
    return max(offsets, default=0)


def settings(cfg):
    """cfg['dmx'] completed with DEFAULTS and checked; `channels` parsed."""
    dmx = {**DEFAULTS, **(cfg.get('dmx') or {})}
    if dmx['source'] not in SOURCES:
        raise ValueError(f'dmx.source is one of {", ".join(SOURCES)}')
    dmx['universe'] = int(dmx['universe'])
    dmx['address'] = int(dmx['address'])
    try:
        warm, cool = (float(v) for v in dmx['cct'])
    except (TypeError, ValueError):
        raise ValueError('dmx.cct is [warm K, cool K]') from None
    if not 1000 <= warm < cool <= 40000:
        raise ValueError('dmx.cct: 1000 <= warm < cool <= 40000')
    look = cfg.get('look') or {}
    base = float(look.get('temperature', 6500.0))
    if not warm <= base <= cool:
        raise ValueError(f'dmx.cct must enclose look.temperature ({base:g} K)')
    dmx['cct'] = (warm, cool)
    dmx['channels'] = (
        default_channels(cfg) if dmx['channels'] is None else parse_channels(dmx['channels'], cfg)
    )
    span = channel_span(dmx['channels'])
    if not 1 <= dmx['address'] <= 512 - span + 1:
        raise ValueError(f'dmx.address is 1..{512 - span + 1} ({span} channels used)')
    dmx['smooth'] = parse_smooth(dmx['smooth'])
    return dmx


def parse_smooth(value):
    """The time constants (up, down) in seconds, or None for off."""
    if value is False or value is None:
        return None
    try:
        up, down = (
            (float(v) for v in value)
            if isinstance(value, (list, tuple))
            else (float(value), float(value))
        )
    except (TypeError, ValueError):
        raise ValueError(
            'dmx.smooth is false, or time constants in seconds: [up, down] or one'
        ) from None
    if up <= 0 or down <= 0:
        raise ValueError('dmx.smooth: time constants are positive')
    return (up, down)


class Smooth:
    """Channel values that follow the desk's through a low-pass filter:
    per call each closes a fraction 1 - exp(-dt / tau) of the distance to
    its target, tau being `up` when rising and `down` when falling (dt
    capped, so a stall never ends in a jump), and lands on the target
    once within half a value."""

    def __init__(self, taus, initial):
        self.up, self.down = taus
        self.values = dict(initial)  # offset -> float
        self.last = time.monotonic()

    def step(self, targets):
        now = time.monotonic()
        dt = min(now - self.last, 0.1)
        self.last = now
        alphas = (1.0 - math.exp(-dt / self.up), 1.0 - math.exp(-dt / self.down))
        for offset, target in targets.items():
            value = self.values.get(offset, float(target))
            value += alphas[target < value] * (target - value)
            self.values[offset] = float(target) if abs(target - value) < 0.5 else value
        return self.values


def offset_labels(cfg, channels):
    """{offset: [line, line]} for the faders: the function and the objects
    on that offset ('canvas' / '1+3'), or the global name."""
    out = {}
    for name in GLOBAL_CHANNELS:
        if channels[name]:
            out.setdefault(channels[name], []).append((name, None))
    for oid, spec in channels['objects'].items():
        for name, off in spec.items():
            out.setdefault(off, []).append((name, oid))
    labels = {}
    for off, entries in out.items():
        functions = list(dict.fromkeys(n for n, _ in entries))
        ids = [str(oid) for _, oid in entries if oid is not None]
        labels[off] = ['/'.join(functions), '+'.join(ids)]
    return labels


def channel_labels(cfg, channels):
    """[(offset, label)] of the channels in use, by offset."""
    names = {o['id']: o.get('name', o['id']) for o in cfg['objects']}
    labels = [(channels[n], n) for n in GLOBAL_CHANNELS if channels[n]]
    for oid, spec in channels['objects'].items():
        labels += [(off, f'{names[oid]} {name}') for name, off in spec.items()]
    return sorted(labels)


def cct_kelvin(value, warm_cool, base):
    """Colour temperature of the light at full for the cct channel: 128 =
    `base` (look.temperature), None (no cct channel) too; 0..128 runs from
    `warm` to base, 128..255 from base to `cool`, each linear in mired
    (1e6 / K), about how the eye scales it."""
    if value is None:
        return base
    warm, cool = warm_cool
    if value <= 128:
        mired = 1e6 / warm + (1e6 / base - 1e6 / warm) * value / 128.0
    else:
        mired = 1e6 / base + (1e6 / cool - 1e6 / base) * (value - 128) / 127.0
    return 1e6 / mired


class Levels:
    """What the lamps are asked for, per object id: (canvas, molding,
    kelvin): levels 0..1 with the master multiplied in, and the colour
    temperature of the object's lamps at full."""

    __slots__ = ('objects',)

    def __init__(self, objects):
        self.objects = dict(objects)

    @classmethod
    def full(cls, kelvin, ids):
        return cls({i: (1.0, 1.0, float(kelvin)) for i in ids})

    def scaled(self, canvas, molding):
        """These levels with every canvas x `canvas` and every molding x
        `molding` (the blackout schedule on top of the desk)."""
        return Levels({i: (c * canvas, m * molding, k) for i, (c, m, k) in self.objects.items()})

    def is_full(self, kelvin):
        return all(c == 1.0 and m == 1.0 and k == kelvin for c, m, k in self.objects.values())

    def __eq__(self, other):
        return isinstance(other, Levels) and self.objects == other.objects

    __hash__ = None


# --- receivers -------------------------------------------------------------


class Receiver:
    """One DMX universe as it comes in, kept up to date by a thread.
    Subclasses implement _run() (a loop until self._stop) and call _set()
    per frame; an exception there is reported in `error` and _run() is
    retried after two seconds (unplugged widget, no network yet)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = bytes(512)
        self.frames = 0
        self.last = None  # time.monotonic() of the last frame
        self.error = None  # what keeps the source from working, if anything
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._guard, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def snapshot(self):
        """(512 channel values as bytes, number of frames so far)."""
        with self._lock:
            return self._data, self.frames

    def _set(self, slots):
        data = bytes(slots[:512]).ljust(512, b'\0')
        with self._lock:
            self._data = data
            self.frames += 1
            self.last = time.monotonic()
            self.error = None

    def _guard(self):
        while not self._stop.is_set():
            try:
                self._run()
            except Exception as e:  # noqa: BLE001 - anything: report and retry
                self.error = f'{type(e).__name__}: {e}'
                self._stop.wait(2.0)

    def _run(self):
        raise NotImplementedError

    def describe(self):
        raise NotImplementedError


def _udp_socket(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    sock.bind(('', port))
    sock.settimeout(0.5)
    return sock


class SacnReceiver(Receiver):
    """E1.31: the universe's multicast group on every interface (re-joined
    as interfaces appear: the USB network adapter plugged in late), and
    unicast to this host."""

    def __init__(self, universe):
        super().__init__()
        self.universe = universe
        self.group = socket.inet_aton(f'239.255.{universe >> 8}.{universe & 255}')

    def describe(self):
        return f'sACN universe {self.universe}'

    def _join(self, sock, joined):
        for index, _ in socket.if_nameindex():
            if index in joined:
                continue
            mreq = struct.pack('4s4si', self.group, b'\0\0\0\0', index)
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                joined.add(index)
            except OSError:
                pass  # no IPv4 on it (yet)

    def _run(self):
        joined = set()
        with _udp_socket(SACN_PORT) as sock:
            rejoin = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= rejoin:
                    self._join(sock, joined)
                    rejoin = now + 5.0
                try:
                    packet = sock.recv(2048)
                except socket.timeout:
                    continue
                slots = self.parse(packet)
                if slots is not None:
                    self._set(slots)

    def parse(self, p):
        """The slots of an E1.31 data packet for our universe, else None."""
        if len(p) < 126 or p[4:16] != ACN_ID:
            return None
        if p[18:22] != b'\0\0\0\x04' or p[40:44] != b'\0\0\0\x02':
            return None  # not E1.31 data / not DMP data
        if (p[113] << 8 | p[114]) != self.universe:
            return None
        if p[112] & 0xC0:
            return None  # preview data or stream terminated
        if p[117] != 0x02 or p[125] != 0:
            return None  # not a set-property / not dimmer data
        count = p[123] << 8 | p[124]  # start code + slots
        return p[126 : 125 + count]


class ArtnetReceiver(Receiver):
    def __init__(self, universe):
        super().__init__()
        self.universe = universe

    def describe(self):
        return f'Art-Net universe {self.universe}'

    def _run(self):
        with _udp_socket(ARTNET_PORT) as sock:
            while not self._stop.is_set():
                try:
                    packet = sock.recv(2048)
                except socket.timeout:
                    continue
                slots = self.parse(packet)
                if slots is not None:
                    self._set(slots)

    def parse(self, p):
        """The slots of an ArtDmx packet for our universe, else None."""
        if len(p) < 20 or p[:8] != b'Art-Net\0' or p[8] | p[9] << 8 != 0x5000:
            return None
        if (p[15] << 8 | p[14]) != self.universe:
            return None
        length = p[16] << 8 | p[17]
        return p[18 : 18 + length]


def find_port(port):
    """The serial device for dmx.port: as given, or for `auto` the one
    USB serial device that calls itself DMX (the Enttec Pro says "DMX USB
    PRO"), else the only USB serial device there is."""
    if port != 'auto':
        return port
    from serial.tools import list_ports

    ports = [p for p in list_ports.comports() if p.vid is not None]
    dmx = [p for p in ports if 'DMX' in f'{p.product or ""} {p.description or ""}'.upper()]
    pro = [p for p in dmx if 'PRO' in f'{p.product or ""} {p.description or ""}'.upper()]
    for group in (pro, dmx, ports):
        if len(group) == 1:
            return group[0].device
    if not ports:
        raise LookupError('no USB serial device')
    raise LookupError(
        'set dmx.port to one of '
        + ', '.join(f'{p.device} ({p.product or p.description})' for p in ports)
    )


class EnttecReceiver(Receiver):
    """The Enttec DMX USB Pro widget protocol: messages 7E label lenLSB
    lenMSB data E7. The widget sends label 5 per received frame (status,
    start code, slots); label 9 (changed slots only) is decoded too, in
    case a widget was left in that mode."""

    START, END = 0x7E, 0xE7
    RECEIVED, RECEIVE_MODE, CHANGED = 5, 8, 9

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.device = None

    def describe(self):
        return f'Enttec widget on {self.device or self.port}'

    def _run(self):
        import serial

        self.device = find_port(self.port)
        frame = bytearray(513)  # start code + slots, kept for label 9 deltas
        with serial.Serial(self.device, 115200, timeout=0.5) as ser:
            # every frame please (also resets a widget left in on-change mode)
            ser.write(bytes([self.START, self.RECEIVE_MODE, 1, 0, 0, self.END]))
            buf = bytearray()
            while not self._stop.is_set():
                chunk = ser.read(1)
                if not chunk:
                    continue
                buf += chunk + ser.read(ser.in_waiting)
                for label, payload in self.messages(buf):
                    if label == self.RECEIVED:
                        if len(payload) < 2 or payload[0] != 0 or payload[1] != 0:
                            continue  # overflow/overrun, or not dimmer data
                        frame[: len(payload) - 1] = payload[1:]
                        self._set(frame[1:])
                    elif label == self.CHANGED and len(payload) >= 6:
                        start, bits, changed = payload[0], payload[1:6], payload[6:]
                        k = 0
                        for b in range(40):
                            if bits[b >> 3] >> (b & 7) & 1 and k < len(changed):
                                if start * 8 + b < 513:
                                    frame[start * 8 + b] = changed[k]
                                k += 1
                        self._set(frame[1:])

    @classmethod
    def messages(cls, buf):
        """Yield (label, payload) for every complete message at the front
        of `buf`, removing them; junk before a start byte is dropped."""
        while True:
            start = buf.find(cls.START)
            if start < 0:
                buf.clear()
                return
            if start:
                del buf[:start]
            if len(buf) < 5:
                return
            length = buf[2] | buf[3] << 8
            if length > 600:
                del buf[0]
                continue
            if len(buf) < 5 + length:
                return
            if buf[4 + length] != cls.END:
                del buf[0]
                continue
            label, payload = buf[1], bytes(buf[4 : 4 + length])
            del buf[: 5 + length]
            yield label, payload


# --- the demo desk ---------------------------------------------------------


def _snap(**targets):
    return ('snap', targets)


def _fade(**targets):
    return ('fade', targets)


# (name, seconds, what): `snap` sets the functions at the start and holds,
# `fade` runs them linearly from where they are to the targets over the
# segment, 'chase' lights one picture after another. Functions: master,
# cct, canvas, frame (the last two for every object); values 0..255.
DEMO = [
    ('full light, as rendered', 3.0, _snap(master=255, cct=128, canvas=255, frame=255)),
    ('pictures fade out in 5 s, frames stay', 5.0, _fade(canvas=0)),
    ('frames fade out in 5 s', 5.0, _fade(frame=0)),
    ('dark', 1.0, _snap()),
    ('snap on', 2.0, _snap(canvas=255, frame=255)),
    ('blackout: master snaps to 0', 1.5, _snap(master=0)),
    ('snap on', 2.0, _snap(master=255)),
    ('warm: cct to 2250 K in 3 s', 3.0, _fade(cct=1)),
    ('warm to cold: cct to 5500 K in 8 s', 8.0, _fade(cct=255)),
    ('back to 3200 K in 3 s', 3.0, _fade(cct=128)),
    ('chase: one picture at a time', 9.0, ('chase', {})),
    ('master fades to black in 6 s', 6.0, _fade(master=0)),
    ('dark', 1.5, _snap()),
]


class DemoReceiver(Receiver):
    """A desk of our own: plays DEMO on a loop into the fixture's channel
    map, 40 frames a second, so the whole path from 8-bit frames through
    the smoothing to the lamps runs as with a real desk."""

    FPS = 40.0

    def __init__(self, dmx):
        super().__init__()
        self.address = dmx['address']
        self.channels = dmx['channels']
        self.segment = DEMO[0][0]
        self.total = sum(seconds for _, seconds, _ in DEMO)

    def describe(self):
        return f'demo ({self.segment})'

    def state_at(self, t):
        """The functions' values at t seconds into the loop, and the
        segment's name. Runs the segments up to t, so a fade starts from
        wherever the previous ones left things."""
        ids = list(self.channels['objects'])
        state = {'master': 255.0, 'cct': 0.0}
        state.update({('canvas', oid): 255.0 for oid in ids})
        state.update({('frame', oid): 255.0 for oid in ids})

        def apply(targets, fraction):
            for name, target in targets.items():
                keys = [name] if name in ('master', 'cct') else [(name, oid) for oid in ids]
                for key in keys:
                    state[key] = start[key] + (target - start[key]) * fraction

        elapsed = 0.0
        for name, seconds, (kind, targets) in DEMO:
            start = dict(state)
            local = min(max(t - elapsed, 0.0), seconds)
            if kind == 'snap':
                apply(targets, 1.0)
            elif kind == 'fade':
                apply(targets, local / seconds)
            elif kind == 'chase' and t >= elapsed:
                # each picture gets a slot: up, hold, down; the frames dim to half
                slot = seconds / len(ids)
                for k, oid in enumerate(ids):
                    phase = (local - k * slot) / slot
                    state[('canvas', oid)] = 255.0 * max(0.0, 1.0 - abs(2.0 * phase - 1.0) * 1.5)
                    state[('frame', oid)] = 128.0
                if local >= seconds:  # leave everything back on
                    for oid in ids:
                        state[('canvas', oid)] = state[('frame', oid)] = 255.0
            elapsed += seconds
            if t < elapsed:
                return state, name
        return state, DEMO[-1][0]

    def universe_at(self, t):
        state, name = self.state_at(t)
        data = bytearray(512)
        first = self.address - 1

        def put(offset, value):
            if offset:  # shared offsets merge highest-takes-precedence, as desks do
                i = first + offset - 1
                data[i] = max(data[i], int(round(min(max(value, 0.0), 255.0))))

        put(self.channels['master'], state['master'])
        put(self.channels['cct'], state['cct'])
        for oid, spec in self.channels['objects'].items():
            put(spec.get('canvas'), state[('canvas', oid)])
            put(spec.get('frame'), state[('frame', oid)])
            put(spec.get('power'), 255.0)
        return bytes(data), name

    def _run(self):
        t0 = time.monotonic()
        while not self._stop.is_set():
            data, self.segment = self.universe_at((time.monotonic() - t0) % self.total)
            self._set(data)
            self._stop.wait(1.0 / self.FPS)


def open_receiver(dmx):
    """A started receiver for the dmx settings, or None when off."""
    source = dmx['source']
    if source == 'off':
        return None
    if source == 'sacn':
        return SacnReceiver(dmx['universe']).start()
    if source == 'artnet':
        return ArtnetReceiver(dmx['universe']).start()
    if source == 'demo':
        return DemoReceiver(dmx).start()
    return EnttecReceiver(dmx['port']).start()


# --- the desk --------------------------------------------------------------


class Desk:
    """The light desk's view of the projection: the fixture's channels,
    decoded to Levels. `on` is False for dmx.source off; levels() then are
    full, and so they are until the desk's first frame; a channel stays
    at its initial value until the desk changes it (see `first`)."""

    def __init__(self, cfg, receiver=None):
        from . import render

        self.settings = settings(cfg)
        self.channels = self.settings['channels']
        self.ids = [o['id'] for o in cfg['objects']]
        self.labels = channel_labels(cfg, self.channels)
        self.kelvin = float(render.look_settings(cfg)['temperature'])
        self.receiver = receiver if receiver is not None else open_receiver(self.settings)
        # the offsets in use; before the desk's first frame (or without a
        # desk) the channels stand at "full light, as rendered" (cct 128,
        # the middle of the fader, is look.temperature), and the
        # filter starts there, so the first frame is faded to, not jumped to
        self.offsets = sorted({off for off, _ in self.labels})
        self.offset_labels = offset_labels(cfg, self.channels)
        self.initial = {
            off: 128.0 if off == self.channels['cct'] else 255.0 for off in self.offsets
        }
        self.smooth = (
            Smooth(self.settings['smooth'], self.initial) if self.settings['smooth'] else None
        )
        self.override = {}  # offset -> (value by hand, the desk's value it took over from)
        # offset -> the first value the desk sent; while a channel still
        # sends that it is not touched yet and the initial value holds
        self.first = {}
        self.touched = set()

    @property
    def on(self):
        return self.receiver is not None

    def close(self):
        if self.receiver is not None:
            self.receiver.stop()

    @property
    def receiving(self):
        """True once the desk has sent a frame."""
        return self.receiver is not None and self.receiver.frames > 0

    def targets(self):
        """What the desk sends, by offset (the initial full light before
        its first frame, or without a desk, and for a channel it has not
        changed yet)."""
        targets = dict(self.initial)
        if self.receiver is not None:
            data, frames = self.receiver.snapshot()
            if frames:
                first = self.settings['address'] - 1
                for off in self.offsets:
                    value = data[first + off - 1]
                    if self.first.setdefault(off, value) != value:
                        self.touched.add(off)
                    if off in self.touched:
                        targets[off] = float(value)
        return targets

    def set_override(self, offset, value):
        """Take a channel over by hand (the on-screen faders): it holds
        until the desk moves that channel itself."""
        self.override[offset] = (min(max(float(value), 0.0), 255.0), self.targets()[offset])

    def frame(self):
        """The fixture's channel values by offset, as the lamps should
        follow them now: the desk's (or the hand's) targets, filtered."""
        targets = self.targets()
        for offset, (value, base) in list(self.override.items()):
            if targets[offset] != base:
                del self.override[offset]  # the desk moved: it takes over again
            else:
                targets[offset] = value
        return self.smooth.step(targets) if self.smooth else targets

    def _value(self, data, offset):
        """A channel's value by offset from the address; None when the
        channel is not in the map."""
        if not offset:
            return None
        return data[offset]

    def _level(self, data, offset):
        value = self._value(data, offset)
        return 1.0 if value is None else value / 255.0

    def levels(self):
        data = self.frame()
        master = self._level(data, self.channels['master'])
        kelvin = cct_kelvin(
            self._value(data, self.channels['cct']), self.settings['cct'], self.kelvin
        )
        objects = {}
        for oid in self.ids:
            spec = self.channels['objects'][oid]
            power = master * self._level(data, spec.get('power'))
            objects[oid] = (
                power * self._level(data, spec.get('canvas')),
                power * self._level(data, spec.get('frame')),
                kelvin,
            )
        return Levels(objects)

    def status_lines(self):
        """Two lines for the info overlay: the source and its signal, and
        the channel values (per object canvas/frame[/power])."""
        data = self.frame()
        if self.receiver is None:
            head, signal = 'no desk', 'faders by hand'
        else:
            head = f'{self.receiver.describe()} @{self.settings["address"]}'
            if self.receiver.error:
                return [f'{head}: {self.receiver.error}']
            if not self.receiver.frames:
                signal = 'waiting for the desk'
            else:
                age = time.monotonic() - self.receiver.last
                signal = (
                    f'no signal for {age:.0f} s' if age > 2.0 else f'{self.receiver.frames} frames'
                )
        parts = []
        if self.channels['master']:
            parts.append(f'master {self._value(data, self.channels["master"]):.0f}')
        if self.channels['cct']:
            kelvin = cct_kelvin(
                self._value(data, self.channels['cct']), self.settings['cct'], self.kelvin
            )
            parts.append(f'cct {kelvin:.0f} K')
        for oid, spec in self.channels['objects'].items():
            values = '/'.join(
                f'{self._value(data, spec[n]):.0f}' for n in OBJECT_CHANNELS if n in spec
            )
            parts.append(f'{oid}: {values}')
        return [f'{head} ({signal})', '  '.join(parts)]

    def status(self):
        """One line for the monitor."""
        return ': '.join(self.status_lines())


def main():
    from . import render

    ap = argparse.ArgumentParser(
        description='Show the DMX channels the desk sends to the projection'
    )
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--source', choices=SOURCES[1:], help='override dmx.source')
    ap.add_argument('--universe', type=int, help='override dmx.universe')
    ap.add_argument('--port', help='override dmx.port')
    args = ap.parse_args()
    cfg = render.load_config(args.config)
    cfg['dmx'] = {
        **(cfg.get('dmx') or {}),
        **{
            k: v
            for k, v in vars(args).items()
            if k in ('source', 'universe', 'port') and v is not None
        },
    }
    desk = Desk(cfg)
    if not desk.on:
        sys.exit('dmx.source is off (config.yaml, or --source)')
    print(
        f'{desk.receiver.describe()}, fixture at {desk.settings["address"]}: '
        + ', '.join(f'{off} {label}' for off, label in desk.labels)
    )
    try:
        while True:
            print('\r' + desk.status().ljust(140)[:140], end='', flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()
    finally:
        desk.close()


if __name__ == '__main__':
    main()
