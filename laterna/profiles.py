"""Device profiles: the projector and the DMX in, a file each.

A show can keep what depends on the venue out of its config.yaml, in
files next to it:

  projector.yaml  the beamer: canvas, scale_mm_per_px, rotation,
                  image_offset, keystone, position and distance (of
                  `projector:`), gamma, white (look.white), curve (the
                  dimmer curve, dmx.curve)
  dmx-in.yaml     the desk: source, universe, port, address, channels,
                  cct, smooth, start (of `dmx:`)

Each file holds shared settings at the top, `profiles` by name (each
laid over the shared ones: a key it sets wins) and `profile`, the one in
use (default the first):

  profile: theater
  cct: [2250, 6500]
  profiles:
    theater: {source: enttec, address: 101, channels: {...}}
    test: {source: enttec, address: 1, channels: {...}}

The DMX in can always be switched `off` too (no desk), without a
profile for it. render.load_config lays the chosen profiles
into the config, so the apps read cfg['canvas'] as ever; a key a file
owns must then not be in config.yaml as well. calibration.save_config
writes what an app changed back where it came from (the profile, or the
shared part), so global alignment saves the keystone into the profile
in use. The app menu shows the profiles and switches them (P / I),
changing only the `profile:` line, so the file keeps its comments.
Without these files everything stays in config.yaml, as before.
"""

import copy
import pathlib
import re

import yaml

from . import schema

OFF = 'off'


class Kind:
    def __init__(self, name, file, label, key, keys, off=None):
        self.name = name
        self.file = file
        self.label = label  # for the menu
        self.key = key  # the menu's key that switches it
        self.keys = keys  # the file's keys -> their path in the config
        self.off = off  # the settings of the built-in `off`, None: no off


PROJECTOR = Kind(
    'projector',
    'projector.yaml',
    'projector',
    'p',
    {
        'canvas': ('canvas',),
        'scale_mm_per_px': ('scale_mm_per_px',),
        'rotation': ('rotation',),
        'image_offset': ('image_offset',),
        'keystone': ('keystone',),
        'position': ('projector', 'position'),
        'distance': ('projector', 'distance'),
        'gamma': ('gamma',),
        'white': ('look', 'white'),
        'curve': ('dmx', 'curve'),
    },
)
DMX_IN_KEYS = ('source', 'universe', 'port', 'address', 'channels', 'cct', 'smooth', 'start')
DMX_IN = Kind(
    'dmx_in',
    'dmx-in.yaml',
    'dmx in',
    'i',
    {k: ('dmx', k) for k in DMX_IN_KEYS},
    off={'source': 'off'},
)
KINDS = (PROJECTOR, DMX_IN)
BY_NAME = {k.name: k for k in KINDS}


# --- nested dicts by path ----------------------------------------------------

_MISSING = object()


def get_path(data, path):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return _MISSING
        data = data[key]
    return data


def set_path(data, path, value):
    for key in path[:-1]:
        data = data.setdefault(key, {})
    data[path[-1]] = value


def pop_path(data, path):
    """Remove and return the value at `path` (_MISSING if none), dropping
    the dicts it leaves empty."""
    if len(path) == 1:
        return data.pop(path[0], _MISSING) if isinstance(data, dict) else _MISSING
    child = data.get(path[0]) if isinstance(data, dict) else None
    if not isinstance(child, dict):
        return _MISSING
    value = pop_path(child, path[1:])
    if not child:
        del data[path[0]]
    return value


# --- reading -----------------------------------------------------------------


def read_file(path, kind):
    """A profile file, checked: {'shared': {...}, 'profiles': {name:
    {...}}, 'profile': name or None} (raw, as the file has it)."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    if isinstance(raw, dict) and raw.get('profile') is False:
        raw['profile'] = OFF  # a bare `off` in YAML is the boolean false
    schema.check(schema.PROFILE_FILES[kind.name], raw, path.name)
    return {
        'shared': {k: v for k, v in raw.items() if k not in ('profile', 'profiles')},
        'profiles': {name: p or {} for name, p in (raw.get('profiles') or {}).items()},
        'profile': raw.get('profile'),
    }


def choices(kind, profiles):
    """The profiles to pick from: the file's, plus the built-in off."""
    names = list(profiles)
    if kind.off is not None and OFF not in names:
        names.append(OFF)
    return names


def apply(data, folder):
    """Lay the chosen profiles of the files in `folder` into the config
    `data` (config.yaml as loaded, changed in place). Returns what
    save() needs: {kind name: {'name', 'choices', 'where': {key:
    'profile' | 'shared'}}} for the files there are."""
    info = {}
    for kind in KINDS:
        path = pathlib.Path(folder) / kind.file
        if not path.exists():
            continue
        f = read_file(path, kind)
        names = choices(kind, f['profiles'])
        if not names:
            raise ValueError(f'{kind.file}: no profiles')
        name = f['profile'] if f['profile'] is not None else names[0]
        if name not in names:
            raise ValueError(f'{kind.file}: profile {name} is not one of {", ".join(names)}')
        chosen = f['profiles'].get(name, kind.off if name == OFF else {})
        for key, target in kind.keys.items():
            if get_path(data, target) is not _MISSING:
                raise ValueError(
                    f'config.yaml: {".".join(target)} is in {kind.file} now, remove it here'
                )
        where = {}
        for key, value in {**f['shared'], **chosen}.items():
            set_path(data, kind.keys[key], copy.deepcopy(value))
            where[key] = 'profile' if key in chosen else 'shared'
        info[kind.name] = {'name': name, 'choices': names, 'where': where}
    return info


# --- writing -----------------------------------------------------------------


def save(data, info, folder):
    """Move what the profile files own out of `data` (the config about to
    be saved, changed in place) back into their files: into the profile
    in use, or the shared part where the key came from there. A file is
    only written when something in it changed (so it keeps its comments
    otherwise); the built-in off has nothing to keep."""
    for kind in KINDS:
        if kind.name not in info:
            continue
        values = {key: pop_path(data, target) for key, target in kind.keys.items()}
        state = info[kind.name]
        path = pathlib.Path(folder) / kind.file
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        if state['name'] == OFF and state['name'] not in (raw.get('profiles') or {}):
            continue
        profiles = raw.setdefault('profiles', {}) or {}
        raw['profiles'] = profiles
        profile = profiles.get(state['name']) or {}
        profiles[state['name']] = profile
        changed = False
        for key, value in values.items():
            target = raw if state['where'].get(key) == 'shared' else profile
            if value is _MISSING:
                if key in target:
                    del target[key]
                    changed = True
            elif target.get(key, _MISSING) != value:
                target[key] = value
                changed = True
        if changed:
            with open(path, 'w') as f:
                yaml.dump(
                    raw, f, sort_keys=False, default_flow_style=None, width=100, allow_unicode=True
                )


def select(folder, kind, name):
    """Make `name` the profile in use: only the file's `profile:` line
    changes (added at the top when there is none)."""
    path = pathlib.Path(folder) / kind.file
    text = path.read_text()
    line = f'profile: {name}'
    pattern = re.compile(r'^profile:.*$', re.MULTILINE)
    text = pattern.sub(line, text, count=1) if pattern.search(text) else f'{line}\n{text}'
    path.write_text(text)


def cycle(folder, kind, info):
    """Switch `kind` to its next profile (in file order, then off); returns
    the new name, or None when there is no file for it."""
    state = info.get(kind.name)
    if state is None:
        return None
    names = state['choices']
    name = names[(names.index(state['name']) + 1) % len(names)]
    select(folder, kind, name)
    return name


def summary(info):
    """'P projector: theater   I dmx in: test' for the menu; '' without files."""
    return '   '.join(
        f'{kind.key.upper()} {kind.label}: {info[kind.name]["name"]}'
        for kind in KINDS
        if kind.name in info
    )


def texts(folder):
    """[(file name, text)] of the profile files there are (for the export)."""
    out = []
    for kind in KINDS:
        path = pathlib.Path(folder) / kind.file
        if path.exists():
            out.append((kind.file, path.read_text()))
    return out
