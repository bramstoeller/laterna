"""Evens out the brightness of a folder of pictures: dark pictures are
lifted towards one common key, so that a series reads as one, without
clipping the highlights.

    python -m tools.balance_brightness SOURCE DEST [--skip NAME ...]

Reads every PNG in SOURCE and writes the brightened picture under the same
name in DEST (created if needed; SOURCE and DEST may not be the same
folder, so the originals stay untouched). Pictures named with --skip (the
file name without .png; dark on purpose, like text on black) are copied
unchanged. Prints the key before and after per picture.

The key of a picture is the log-average of its luminance in linear light,
leaving out the (nearly) black and the clipped pixels. The picture is
moved a fraction STRENGTH of the way to TARGET (in log space), and only
ever lifted, never darkened. The lift is a tone curve on the luminance,
g*y / (1 + (g-1)*y): it multiplies the shadows by g and leaves white
white, so the highlights are compressed instead of clipped. The colour of
every pixel is kept (all channels are scaled alike, and never beyond the
channel that reaches 1 first), so the white balance stays as it was.
"""

import argparse
import pathlib
import shutil

import numpy as np
from PIL import Image

from .balance_white import LUMA, to_linear, to_srgb

TARGET = 0.11  # the common key: log-average luminance in linear light
STRENGTH = 0.75  # fraction of the way to TARGET (in log space); 1 = all the way
GAIN_MAX = 6.0  # highest gain in the shadows
CLIPPED = 0.97  # luminance above which pixels are clipped and ignored
DARK = 0.002  # luminance below which pixels are left out of the key
EPS = 1e-6


def key(luma):
    """The log-average of the usable luminances."""
    usable = luma[(luma > DARK) & (luma < CLIPPED)]
    return float(np.exp(np.log(usable).mean())) if usable.size else 0.0


def curve(luma, g):
    """The tone curve: slope g in the shadows, 1 stays 1."""
    return g * luma / (1 + (g - 1) * luma)


def shadow_gain(luma):
    """The shadow gain that brings the key its STRENGTH of the way to
    TARGET (by bisection; the key rises with the gain), 1 when the picture
    is already that bright, at most GAIN_MAX."""
    before = key(luma)
    if before <= 0 or before >= TARGET:
        return 1.0
    goal = before * (TARGET / before) ** STRENGTH
    if key(curve(luma, GAIN_MAX)) <= goal:
        return GAIN_MAX
    lo, hi = 1.0, GAIN_MAX
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if key(curve(luma, mid)) < goal else (lo, mid)
    return (lo + hi) / 2


def brighten(source, dest):
    """Brighten one picture file into another; returns (key before, gain,
    key after)."""
    linear = to_linear(np.asarray(Image.open(source).convert('RGB'), float) / 255)
    luma = linear @ LUMA
    g = shadow_gain(luma)
    if g == 1.0:
        shutil.copyfile(source, dest)
        return key(luma), g, key(luma)
    scale = np.minimum(curve(luma, g) / np.maximum(luma, EPS), 1 / np.maximum(linear.max(-1), EPS))
    out = linear * scale[..., None]
    Image.fromarray((to_srgb(out) * 255 + 0.5).astype(np.uint8)).save(dest, optimize=True)
    return key(luma), g, key(out @ LUMA)


def main():
    ap = argparse.ArgumentParser(description='Even out the brightness of a folder of pictures')
    ap.add_argument('source', type=pathlib.Path, help='folder with the original PNGs')
    ap.add_argument('dest', type=pathlib.Path, help='folder for the brightened PNGs')
    ap.add_argument(
        '--skip', nargs='*', default=[], metavar='NAME', help='pictures to copy unchanged'
    )
    args = ap.parse_args()
    if args.source.resolve() == args.dest.resolve():
        ap.error('SOURCE and DEST must differ; make a copy of the originals first')
    args.dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(args.source.glob('*.png')):
        if path.stem in args.skip:
            shutil.copyfile(path, args.dest / path.name)
            print(f'{path.stem:32s} skipped')
            continue
        before, g, after = brighten(path, args.dest / path.name)
        print(f'{path.stem:32s} key {before:.3f} -> {after:.3f}  gain {g:.2f}')


if __name__ == '__main__':
    main()
