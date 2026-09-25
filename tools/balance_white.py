"""Evens out the white balance of a folder of pictures: every picture is
moved towards one common, mildly warm white, so that a set of pictures
that are all warm, but each in its own way, reads as one series.

    python -m tools.balance_white SOURCE DEST

Reads every PNG in SOURCE and writes the balanced picture under the same
name in DEST (created if needed; SOURCE and DEST may not be the same
folder, so the originals stay untouched). Prints the gains per picture.

Per picture, the colour cast is estimated in linear light from the
brightest pixels (white patch) and the mean of the mid-tones (grey
world); the geometric mean of both is less fooled by a sunset or a blue
sky than either alone. The picture is then scaled per channel towards
TARGET, with the luminance kept. Pictures are only ever cooled, never
warmed, so a picture that is cool on purpose keeps its colour, and the
gains are limited, so a dark sepia picture stays sepia instead of
turning grey-blue.
"""

import argparse
import pathlib

import numpy as np
from PIL import Image

TARGET = np.array([1.25, 1.0, 0.60])  # the common white: R/G, 1, B/G in linear light
STRENGTH = 0.75  # fraction of the way to TARGET (in log space); 1 = all the way
RED_MIN = 0.82  # lowest red gain
BLUE_MAX = 1.7  # highest blue gain
HIGHLIGHTS = 0.95  # quantile of the luminance above which pixels count as highlights
CLIPPED = 0.97  # luminance above which pixels are clipped and ignored
DARK = 0.02  # luminance below which pixels are left out of the grey world

LUMA = np.array([0.2126, 0.7152, 0.0722])  # Rec. 709 luminance weights


def to_linear(srgb):
    """sRGB in 0..1 to linear light."""
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def to_srgb(linear):
    """Linear light to sRGB in 0..1, clipped."""
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)


def estimate_cast(linear):
    """The colour of the light in a picture as (R/G, 1, B/G): the geometric
    mean of the highlights' colour and the mid-tones' mean colour."""
    pixels = linear.reshape(-1, 3)
    luma = pixels @ LUMA
    usable = luma < CLIPPED
    highlights = pixels[usable & (luma >= np.quantile(luma[usable], HIGHLIGHTS))].mean(0)
    grey_world = pixels[usable & (luma > DARK)].mean(0)
    return np.sqrt(highlights / highlights[1] * grey_world / grey_world[1])


def gains(cast):
    """Per-channel gains that move `cast` towards TARGET: only cooler, limited,
    and normalised so that the luminance of the cast colour stays the same."""
    g = (TARGET / cast) ** STRENGTH
    g[0] = np.clip(g[0], RED_MIN, 1.0)
    g[2] = np.clip(g[2], 1.0, BLUE_MAX)
    return g / ((LUMA * g * cast).sum() / (LUMA * cast).sum())


def balance(source, dest):
    """Balance one picture file into another."""
    linear = to_linear(np.asarray(Image.open(source).convert('RGB'), float) / 255)
    g = gains(estimate_cast(linear))
    out = (to_srgb(linear * g) * 255 + 0.5).astype(np.uint8)
    Image.fromarray(out).save(dest, optimize=True)
    return g


def main():
    ap = argparse.ArgumentParser(description='Even out the white balance of a folder of pictures')
    ap.add_argument('source', type=pathlib.Path, help='folder with the original PNGs')
    ap.add_argument('dest', type=pathlib.Path, help='folder for the balanced PNGs')
    args = ap.parse_args()
    if args.source.resolve() == args.dest.resolve():
        ap.error('SOURCE and DEST must differ; make a copy of the originals first')
    args.dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(args.source.glob('*.png')):
        r, g, b = balance(path, args.dest / path.name)
        print(f'{path.stem:32s} gains R {r:.2f}  G {g:.2f}  B {b:.2f}')


if __name__ == '__main__':
    main()
