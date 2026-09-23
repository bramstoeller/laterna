"""The shape of config.yaml and scenes.yaml, checked with pydantic.

Every node refuses keys it does not know (a typo used to fall back to a
default unnoticed: `transiton_time: 10` gave a 1 second fade), and every
value is checked for type and range, so a mistake shows up when the show
is opened, not on the night. Object ids used in scenes.yaml must exist in
config.yaml.

The defaults stay with the code that uses them (render.LOOK_DEFAULTS,
dmx.DEFAULTS, ...): the models only say what may be set. load_config and
parse_stages hand on the data as plain dicts, holding just the keys the
file sets (normalised: numbers as floats, `source: off` as 'off').
"""

import difflib
import json
import pathlib
import sys
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

Positive = Annotated[float, Field(gt=0)]
NonNegative = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Point = Annotated[list[float], Field(min_length=2, max_length=2)]
Channel = Annotated[int, Field(ge=1, le=512)]
Rgb = Annotated[list[Annotated[int, Field(ge=0, le=255)]], Field(min_length=3, max_length=3)]


class Node(BaseModel):
    model_config = ConfigDict(extra='forbid')


# --- config.yaml -------------------------------------------------------------


class Frame(Node):
    border: Positive  # plank width, mm
    inner: Annotated[list[Point], Field(min_length=3)]  # inner corners of the wood


class Object(Node):
    id: int
    name: str | None = None
    description: str | None = None
    frame: Frame
    origin: Point
    scale: Positive | None = None
    rotation: float | None = None


class Projector(Node):
    position: Point  # the lens's foot point on the frame plane, world mm
    distance: Positive


class Border(Node):
    profile: str | Annotated[list[Point], Field(min_length=2)] | None = None
    relief: NonNegative | None = None
    shadow: Fraction | None = None

    @field_validator('profile')
    @classmethod
    def known_profile(cls, value):
        from .render import PROFILES

        if isinstance(value, str) and value not in PROFILES:
            raise ValueError(f'unknown profile {value!r} (known: {", ".join(PROFILES)})')
        return value


class Light(Node):
    azimuth: float | None = None
    elevation: float | None = None
    ambient: NonNegative | None = None
    specular: NonNegative | None = None
    shininess: Positive | None = None
    patina: NonNegative | None = None


class Spot(Node):
    type: Literal['cone', 'gaussian'] | None = None
    strength: Fraction | None = None
    position: Point | None = None
    aim: Point | None = None
    distance: Positive | None = None
    angle: Annotated[float, Field(gt=0, lt=90)] | None = None
    softness: Fraction | None = None
    falloff: NonNegative | None = None
    size: Annotated[list[Positive], Field(min_length=2, max_length=2)] | None = None
    color: Rgb | None = None
    images: NonNegative | None = None
    fill: NonNegative | None = None


class Look(Node):
    molding: NonNegative | None = None
    fill: NonNegative | None = None
    images: NonNegative | None = None
    temperature: Annotated[float, Field(ge=1000, le=40000)] | None = None
    white: Annotated[float, Field(ge=1000, le=40000)] | None = None
    spot_collapse: Fraction | None = None
    plank_depth: NonNegative | None = None
    spot: Spot | None = None
    molding_spot: Spot | None = None


class ObjectChannels(Node):
    canvas: Channel | None = None
    frame: Channel | None = None
    molding: Channel | None = None  # the older name of frame
    power: Channel | None = None


class Channels(Node):
    master: Channel | None = None
    cct: Channel | None = None
    objects: dict[int | str, ObjectChannels] | None = None  # by object id or name


class Dmx(Node):
    source: Literal['off', 'sacn', 'artnet', 'enttec', 'demo'] | None = None
    universe: Annotated[int, Field(ge=0, le=63999)] | None = None
    port: str | None = None  # the Enttec's serial port, or auto
    address: Channel | None = None
    cct: Annotated[list[Positive], Field(min_length=2, max_length=2)] | None = None
    smooth: Annotated[list[NonNegative], Field(min_length=2, max_length=2)] | None = None
    channels: Channels | None = None

    @field_validator('source', mode='before')
    @classmethod
    def bare_off(cls, value):
        # a bare `off` in YAML is the boolean false
        return 'off' if value is False else value


class Config(Node):
    description: str | None = None
    scale_mm_per_px: Positive
    canvas: Annotated[list[Annotated[int, Field(gt=0)]], Field(min_length=2, max_length=2)]
    rotation: float | None = None
    image_offset: Point | None = None
    # projector px the canvas corners move (top left, top right, bottom
    # right, bottom left): the keystone correction, step 3
    keystone: Annotated[list[Point], Field(min_length=4, max_length=4)] | None = None
    projector: Projector | None = None
    border: Border
    light: Light | None = None
    objects: Annotated[list[Object], Field(min_length=1)]
    gamma: Positive | None = None
    look: Look | None = None
    dmx: Dmx | None = None

    @model_validator(mode='after')
    def unique_ids(self):
        ids = [o.id for o in self.objects]
        doubles = sorted({i for i in ids if ids.count(i) > 1})
        if doubles:
            raise ValueError(f'object ids must be unique: {", ".join(map(str, doubles))} twice')
        return self


# --- scenes.yaml -------------------------------------------------------------


class Timing(Node):
    hold: Positive | None = None  # seconds before moving on; absent: wait for a key
    transition: Literal['fade'] | None = None
    transition_time: NonNegative | None = None


class Mapping(Timing):
    image: str | None = None
    video: str | None = None
    slideshow: Annotated[list[str], Field(min_length=1)] | None = None
    objects: Annotated[list[int], Field(min_length=1)]
    fit: list[int] | None = None
    width: Positive | None = None
    height: Positive | None = None

    @model_validator(mode='after')
    def one_medium(self):
        if sum(m is not None for m in (self.image, self.video, self.slideshow)) != 1:
            raise ValueError('a mapping needs exactly one of image, video or slideshow')
        return self

    @field_validator('objects', 'fit')
    @classmethod
    def known_objects(cls, value, info: ValidationInfo):
        ids = (info.context or {}).get('ids')
        unknown = [i for i in value or [] if ids is not None and i not in ids]
        if unknown:
            raise ValueError(f'unknown object {", ".join(map(str, unknown))}')
        return value


class Stage(Timing):
    name: str | None = None
    description: str | None = None
    mappings: list[Mapping] | None = None
    blackout: Literal[True] | None = None
    molding_color: Rgb | None = None
    fill_color: Rgb | None = None

    @model_validator(mode='after')
    def contents(self):
        if self.blackout:
            if self.mappings or self.molding_color or self.fill_color:
                raise ValueError('a blackout stage shows nothing: no mappings, no colours')
        elif self.mappings is None:
            raise ValueError('a stage needs mappings (or blackout: true)')
        used = set()
        for m in self.mappings or []:
            for i in m.objects:
                if i in used:
                    raise ValueError(f'object {i} mapped twice')
                used.add(i)
        return self


class Fade(Node):
    """A `- fade: <seconds>` entry between two stages."""

    fade: NonNegative


def _entry_kind(entry):
    if isinstance(entry, dict) and 'fade' in entry and 'mappings' not in entry:
        if not entry.get('blackout'):
            return 'fade'
    return 'stage'


class Scenes(Timing):
    description: str | None = None
    fade: NonNegative | None = None  # the older name of transition_time
    stages: list[
        Annotated[
            Annotated[Stage, Tag('stage')] | Annotated[Fade, Tag('fade')],
            Discriminator(_entry_kind),
        ]
    ]

    @model_validator(mode='after')
    def one_fade_time(self):
        if self.fade is not None and self.transition_time is not None:
            raise ValueError('fade and transition_time mean the same, use one')
        return self


# --- loading -------------------------------------------------------------------


def _known_keys():
    """Every key any node knows: the vocabulary for 'did you mean'."""
    keys = set()
    for model in Node.__subclasses__() + Timing.__subclasses__() + [Timing]:
        keys |= set(model.model_fields)
    return sorted(keys)


def _where(loc, data, what):
    """A readable place for an error: 'stage opening, mapping 2' rather
    than stages.1.mappings.1."""
    parts, node = [], data
    # drop the union's tags: stages.<n>.stage.<field> -> stages.<n>.<field>
    loc = [
        p
        for i, p in enumerate(loc)
        if not (i and isinstance(loc[i - 1], int) and p in ('stage', 'fade'))
    ]
    for i, part in enumerate(loc):
        parent = loc[i - 1] if i else None
        if isinstance(part, int) and isinstance(node, list) and part < len(node):
            item = node[part]
            label = item.get('name') or item.get('id') if isinstance(item, dict) else None
            kind = {'stages': 'stage', 'objects': 'object', 'mappings': 'mapping'}.get(parent)
            if kind:
                parts[-1] = f'{kind} {label if label is not None else part + 1},'
            else:
                parts.append(f'item {part + 1}')
            node = item
        else:
            parts.append(str(part))
            node = node.get(part) if isinstance(node, dict) else None
    place = '.'.join(parts).replace(',.', ', ').rstrip(',')
    return f'{what}: ' + (place or 'top level')


def explain(error: ValidationError, data, what):
    """The errors of a validation as a few readable lines."""
    lines = []
    for e in error.errors():
        loc = list(e['loc'])
        if e['type'] == 'extra_forbidden':
            key = str(loc.pop())
            near = difflib.get_close_matches(key, _known_keys(), n=1)
            hint = f' (did you mean {near[0]!r}?)' if near else ''
            lines.append(f'{_where(loc, data, what)}: unknown key {key!r}{hint}')
        else:
            message = e['msg'].removeprefix('Value error, ')
            lines.append(f'{_where(loc, data, what)}: {message}')
    return '\n'.join(dict.fromkeys(lines))


def check(model, data, what, context=None):
    """`data` validated against `model`, as a plain dict of the keys it
    sets; a ValueError with readable lines otherwise."""
    try:
        parsed = model.model_validate(data, context=context)
    except ValidationError as error:
        raise ValueError(explain(error, data, what)) from None
    # warnings off: the serializer cannot follow the callable discriminator
    # of the stages list and warns, though it dumps each entry right
    return parsed.model_dump(exclude_unset=True, warnings=False)


def check_config(data, what='config.yaml'):
    return check(Config, data if data is not None else {}, what)


def check_scenes(data, ids, what='scenes.yaml'):
    return check(Scenes, data if data is not None else {}, what, context={'ids': set(ids)})


def write_json_schemas(folder='.'):
    """config.schema.json and scenes.schema.json, for editors: with the
    YAML language server, `# yaml-language-server: $schema=<file>` at the
    top of a config.yaml or scenes.yaml gives completion and checks."""
    folder = pathlib.Path(folder)
    for name, model in (('config', Config), ('scenes', Scenes)):
        path = folder / f'{name}.schema.json'
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + '\n')
        print(f'written: {path}')


if __name__ == '__main__':
    write_json_schemas(*sys.argv[1:2])
