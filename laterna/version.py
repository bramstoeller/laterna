"""The version the app shows: what it was built from, or runs from.

In order: baked into a build (laterna/_build.py, written by the GitHub
build: `git describe` of the commit), live from git when it runs from a
checkout (v0.3.1 on a tag, v0.3.1-3-ga7a33d7 three commits after it,
-dirty with changes not committed), the installed package's version (pip),
pyproject.toml next to the package; else 'unknown'.
"""

import functools
import pathlib
import subprocess

HERE = pathlib.Path(__file__).resolve().parent


@functools.cache
def version():
    try:
        from ._build import VERSION  # written by .github/workflows/build.yml

        return VERSION
    except ImportError:
        pass
    if (HERE.parent / '.git').exists():
        try:
            out = subprocess.run(
                ['git', 'describe', '--tags', '--always', '--dirty'],
                cwd=HERE.parent,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as installed

        return 'v' + installed('laterna')
    except PackageNotFoundError:
        pass
    try:
        import tomllib

        with open(HERE.parent / 'pyproject.toml', 'rb') as f:
            return 'v' + tomllib.load(f)['project']['version']
    except (OSError, KeyError, ValueError):
        return 'unknown'
