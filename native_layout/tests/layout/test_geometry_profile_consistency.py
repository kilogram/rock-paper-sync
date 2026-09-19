"""Geometry ↔ calibration-profile consistency (provenance by construction).

LAYOUT_VERIFICATION_PLAN.md §1: every DeviceGeometry field must equal (or be
explicitly derived from) a device-measured value in the calibration
profile.json. This test makes constant drift structurally impossible: if
someone edits a measured constant in device.py without re-recording the
corpus — or re-records the corpus without updating device.py — this fails.

Non-corresponding fields are documented in DeviceGeometry.from_profile's
docstring, not silently skipped here.
"""

from pathlib import Path

from native_layout.layout.device import PAPER_PRO_MOVE, DeviceGeometry

PROFILE = (
    Path(__file__).resolve().parents[2]
    / "testdata"
    / "calibration"
    / "paper_pro_move"
    / "profile.json"
)


def test_paper_pro_move_matches_profile():
    """PAPER_PRO_MOVE == geometry constructed from the calibration profile.

    from_profile() overrides every profile-measured field; equality of the two
    objects therefore proves each measured constant in device.py agrees with
    the device recording (firmware-stamped in the profile), and that the
    derived text_pos_x = -text_width/2 relationship holds.
    """
    assert PAPER_PRO_MOVE == DeviceGeometry.from_profile(PROFILE)


def test_from_profile_overrides_measured_fields():
    """from_profile is not a no-op: a wrong base gets corrected by the profile."""
    import dataclasses

    wrong = dataclasses.replace(PAPER_PRO_MOVE, line_height=57.0, text_width=999.0)
    rebuilt = DeviceGeometry.from_profile(PROFILE, base=wrong)
    assert rebuilt.line_height == PAPER_PRO_MOVE.line_height
    assert rebuilt.text_width == PAPER_PRO_MOVE.text_width
    assert rebuilt.text_pos_x == -PAPER_PRO_MOVE.text_width / 2.0
