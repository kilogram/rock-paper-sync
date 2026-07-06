"""Image diff for the bench: grayscale abs-diff heat overlay + pHash distance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image, ImageChops

# Presentation-only constant (heat overlay tint strength)
BASE_DIM = 0.4  # how much the base image is dimmed under the heat map

PHASH_SIZE = 16  # matches the record/replay harness compute_phash default


def compute_phash(image: Image.Image, hash_size: int = PHASH_SIZE) -> imagehash.ImageHash:
    """Perceptual hash on grayscale (same recipe as the record/replay harness)."""
    return imagehash.phash(image.convert("L"), hash_size=hash_size)


@dataclass
class DiffResult:
    """Result of diffing two images."""

    phash_distance: int
    changed_pixels: int
    heat_image: Image.Image


def diff_images(image_a: Image.Image, image_b: Image.Image) -> DiffResult:
    """Compute grayscale abs-diff heat overlay and pHash distance."""
    gray_a = image_a.convert("L")
    gray_b = image_b.convert("L")
    if gray_a.size != gray_b.size:
        raise ValueError(f"Image sizes differ: {gray_a.size} vs {gray_b.size}")

    diff = ImageChops.difference(gray_a, gray_b)
    changed_pixels = sum(1 for px in diff.getdata() if px > 0)

    # Heat overlay: lightened base, tinted red proportional to |a - b|
    base = gray_a.point(lambda v: int(v * BASE_DIM + 255 * (1 - BASE_DIM)))
    heat = Image.merge(
        "RGB",
        (
            ImageChops.screen(base, diff),  # brighten red where images differ
            ImageChops.subtract(base, diff),  # pull green/blue down -> red tint
            ImageChops.subtract(base, diff),
        ),
    )

    distance = compute_phash(image_a) - compute_phash(image_b)
    return DiffResult(phash_distance=int(distance), changed_pixels=changed_pixels, heat_image=heat)


def diff_files(path_a: Path, path_b: Path, out_path: Path | None) -> DiffResult:
    """Diff two PNG files, optionally writing the heat overlay to out_path."""
    with Image.open(path_a) as ia, Image.open(path_b) as ib:
        result = diff_images(ia.copy(), ib.copy())
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result.heat_image.save(out_path, "PNG")
    return result
