# laterna

Projection mapping for stage frames (laterna: Latin for lantern, as in
laterna magica). One projector lights a set of physical picture frames:
laterna draws a virtual gilded molding exactly on the wood, fits
pictures, slideshows and videos inside, lights them with pretend
spotlights and steps through the show's stages on a key press or from a
lighting desk (sACN, Art-Net or an Enttec USB DMX interface).

## Install

Python 3.12 or newer:

```sh
pip install laterna
```

For development, from a checkout:

```sh
python -m venv venv
venv/bin/pip install -e ".[dev]"
venv/bin/pre-commit install
```

Or download a ready build (Linux, Windows) from the Actions tab: the
`laterna-<OS>` artifact is a zip with the executable and its `_internal/`
folder. On Linux, `chmod +x laterna` after unpacking.

## Shows

A show is a folder with a `config.yaml`, a `scenes.yaml`, `images/` and
`videos/`. At startup laterna lists every `config.yaml` below the working
directory (or the folder given as argument; for a build, below the
executable's folder) and asks which show to open. `shows/example/` is a
starting point: copy it, rename it, and put your frames, media and stages
in it.

```sh
venv/bin/laterna            # shows below the working directory
venv/bin/laterna ~/shows    # or below another folder
```

The chosen show's folder becomes the working directory, so each show has
its own `cache/`, `renders/` and `export/`.

## The apps

In show order, each saving what it sets to `config.yaml`:

1. **Test pattern**: static pixel patterns
2. **Dynamic range**: near-black/white steps, gamma chart
3. **Global alignment**: keystone (only for a tilted projector), then scale,
   rotation and position of the image
4. **Shape calibration**: move the frames' corners onto the wood
5. **Look**: brightness per layer, spotlights, colour temperature
6. **Export**: config sheet, run sheet and backup as PDF
7. **Present**: play the stages from `scenes.yaml`

Q / Esc goes back: from an app to the show's menu, from there to the
show list, from there it quits. Each app also runs on its own inside a
show folder, e.g. `python -m laterna.play --test` for a headless self-test.

## Checking a show

`config.yaml` and `scenes.yaml` are checked when a show opens: unknown
keys (with a suggestion), wrong values and object ids that the config
does not have are reported by stage or object name.
`python -m laterna.schema` writes JSON schemas for editor completion.

## Build and release

`.github/workflows/build.yml` lints and builds the Linux and Windows
versions with PyInstaller on every push. Pushing a tag `v<version>`
(matching `version` in `pyproject.toml`) publishes to PyPI through
`.github/workflows/publish.yml`.

## License

MIT: use it for whatever you like. © 2026 Bram Stoeller
<bram.stoeller@brainbuilders.nl>
