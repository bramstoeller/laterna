# laterna

Projection mapping for stage frames (laterna: Latin for lantern, as in
laterna magica). One projector lights a set of physical picture frames:
laterna draws a virtual gilded molding exactly on the frames, fits
pictures, slideshows and videos inside, lights them with pretend
spotlights and steps through the show's scenes on a key press or from a
lighting desk (sACN, Art-Net or an Enttec USB DMX interface).

![The example show: two frames with a public-domain painting each, a gilded
molding drawn on the frames and a pretend spotlight](docs/example.jpg)

*The example show (`shows/example/`), rendered: two frames, a painting in
each, the gilded molding and the pretend spotlights drawn by laterna.*

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

Or download a ready build for Linux or Windows from the
[releases](https://github.com/BramStoeller/laterna/releases): unpack it
and run `laterna` (`laterna.exe`) in the `laterna/` folder; put your shows
next to it.

## Shows

A show is a folder with a `config.yaml`, a `scenes.yaml`, `images/` and
`videos/`. At startup laterna lists every `config.yaml` below the working
directory (or the folder given as argument; for a build, below the
executable's folder) and asks which show to open. `shows/example/` is a
starting point: copy it, rename it, and put your frames, media and scenes
in it.

```sh
venv/bin/laterna            # shows below the working directory
venv/bin/laterna ~/shows    # or below another folder
```

The chosen show's folder becomes the working directory, so each show has
its own `_cache/`, `_renders/` and `_export/`. What the apps make goes in
folders starting with an underscore: those can always go, the rest of a
show folder is yours.

## The apps

In show order, each saving what it sets to `config.yaml`:

1. **Test pattern**: static pixel patterns
2. **Dynamic range**: near-black/white steps, gamma chart
3. **Global alignment**: keystone (only for a tilted projector), then scale,
   rotation and position of the image
4. **Shape calibration**: move the frames' corners onto the real frames
5. **Look**: brightness per layer, spotlights, colour temperature
6. **Export**: config sheet, cue sheet, backup and the scenes' values as PDF,
   and the backup as slides with fades and holds (`backup.pptx`)
7. **Present**: play the scenes from `scenes.yaml`

Q / Esc goes back: from an app to the show's menu, from there to the
show list, from there it quits. Each app also runs on its own inside a
show folder, e.g. `python -m laterna.play --test` for a headless self-test.

## Checking a show

`config.yaml` and `scenes.yaml` are checked when a show opens: unknown
keys (with a suggestion), wrong values and object ids that the config
does not have are reported by scene or object name.
`python -m laterna.schema` writes JSON schemas for editor completion.

## Build and release

Work happens on `develop`; `main` holds what is released.

- `.github/workflows/lint.yml`: the pre-commit hooks, on every push.
- `.github/workflows/build.yml`: the Linux and Windows builds with
  PyInstaller, on pushes to `main`, on pull requests into `main` and by
  hand (Actions → build → Run workflow); not on `develop`.
- `.github/workflows/release.yml`: pushing a tag `v<version>` (matching
  `version` in `pyproject.toml`) makes a GitHub release with
  `laterna-<version>-linux.tar.gz` and `laterna-<version>-windows.zip`
  (plus the wheel and sdist), and publishes to PyPI when the repository
  variable `PUBLISH_PYPI` is `true`.

## License

MIT: use it for whatever you like. © 2026 Bram Stoeller
<bram.stoeller@brainbuilders.nl>
