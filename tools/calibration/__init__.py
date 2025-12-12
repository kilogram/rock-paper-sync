"""Calibration tools for layout engine parameter tuning.

These tools require optional dependencies (PyQt6) for calibration purposes.
They are NOT used at runtime.

Install calibration dependencies with:
    uv pip install "rock-paper-sync[calibration]"

Available tools:
- qt_layout_reference.py: PyQt6-based reference for Qt text layout
- extract_profile.py: Extract device profile from golden .rm files
"""
