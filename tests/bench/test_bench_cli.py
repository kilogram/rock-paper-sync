"""Tests for the bench CLI (python -m rock_paper_sync.bench).

Device-free and fast: renders a small fixture through the real
parse -> generate -> RmRenderer pipeline and checks the CLI surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from rock_paper_sync.bench.__main__ import main
from rock_paper_sync.layout.device import DEFAULT_DEVICE

PAGE_SIZE = (DEFAULT_DEVICE.page_width, int(DEFAULT_DEVICE.page_height))

FIXTURE_MD = """\
# Bench Fixture

This is a small paragraph used by the bench CLI tests. It is long enough
to wrap across more than one visual line on the device so that wrap points
exist for the overlay.

- first item
- second item

Another short paragraph.
"""


@pytest.fixture()
def fixture_md(tmp_path: Path) -> Path:
    md = tmp_path / "fixture.md"
    md.write_text(FIXTURE_MD)
    return md


def _rendered_pngs(outdir: Path) -> list[Path]:
    return sorted(outdir.glob("*.png"))


class TestRender:
    def test_render_produces_full_size_pages(self, fixture_md: Path, tmp_path: Path) -> None:
        outdir = tmp_path / "out"
        rc = main(["render", str(fixture_md), "-o", str(outdir)])
        assert rc == 0

        pngs = _rendered_pngs(outdir)
        assert len(pngs) >= 1
        for png in pngs:
            with Image.open(png) as img:
                assert img.size == PAGE_SIZE

    def test_render_with_overlay_runs(self, fixture_md: Path, tmp_path: Path) -> None:
        plain_dir = tmp_path / "plain"
        overlay_dir = tmp_path / "overlay"
        assert main(["render", str(fixture_md), "-o", str(plain_dir)]) == 0
        assert main(["render", str(fixture_md), "-o", str(overlay_dir), "--overlay"]) == 0

        plain = _rendered_pngs(plain_dir)
        overlaid = _rendered_pngs(overlay_dir)
        assert len(plain) == len(overlaid)
        # Overlay must actually draw something on the page
        with Image.open(plain[0]) as a, Image.open(overlaid[0]) as b:
            assert a.size == b.size == PAGE_SIZE
            assert list(a.getdata()) != list(b.getdata())

    def test_render_missing_file_errors(self, tmp_path: Path) -> None:
        assert main(["render", str(tmp_path / "nope.md"), "-o", str(tmp_path)]) == 2

    def test_render_prints_line_counts_and_timing(
        self, fixture_md: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["render", str(fixture_md), "-o", str(tmp_path / "o")]) == 0
        out = capsys.readouterr().out
        assert "lines" in out
        assert "rendered in" in out


class TestDiff:
    @pytest.fixture()
    def page_png(self, fixture_md: Path, tmp_path: Path) -> Path:
        outdir = tmp_path / "pages"
        assert main(["render", str(fixture_md), "-o", str(outdir)]) == 0
        return _rendered_pngs(outdir)[0]

    def test_identical_images_distance_zero(
        self, page_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        copy = tmp_path / "copy.png"
        with Image.open(page_png) as img:
            img.save(copy, "PNG")
        assert main(["diff", str(page_png), str(copy)]) == 0
        out = capsys.readouterr().out
        assert "pHash distance: 0" in out
        assert "changed pixels: 0" in out

    def test_shifted_image_distance_positive(
        self, page_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        shifted_path = tmp_path / "shifted.png"
        with Image.open(page_png) as img:
            shifted = Image.new("RGB", img.size, (255, 255, 255))
            shifted.paste(img, (40, 60))
            shifted.save(shifted_path, "PNG")

        out_png = tmp_path / "heat.png"
        assert main(["diff", str(page_png), str(shifted_path), "-o", str(out_png)]) == 0
        out = capsys.readouterr().out
        distance = int(out.split("pHash distance: ")[1].split("\n")[0])
        assert distance > 0
        # Heat overlay written and full size
        with Image.open(out_png) as heat:
            assert heat.size == PAGE_SIZE

    def test_diff_missing_file_errors(self, page_png: Path, tmp_path: Path) -> None:
        assert main(["diff", str(page_png), str(tmp_path / "nope.png")]) == 2


class TestPreview:
    def test_preview_renders_after_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text(FIXTURE_MD)
        outdir = tmp_path / "preview"

        assert main(["preview", str(vault), "note.md", "-o", str(outdir)]) == 0
        pngs = sorted(outdir.glob("after_page*.png"))
        assert len(pngs) >= 1
        with Image.open(pngs[0]) as img:
            assert img.size == PAGE_SIZE
        # Honest about the unimplemented reanchor half
        assert "NOT implemented" in capsys.readouterr().out
