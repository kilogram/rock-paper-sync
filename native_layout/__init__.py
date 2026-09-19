"""Native typed-text layout engine for reMarkable devices.

This package contains the native layout engine that predicts reMarkable's
internal xochitl/Qt text layout. It was the original rendering backend for
rock-paper-sync and is preserved here for a potential future native-notebook
project.

Production rock-paper-sync now uses the PDF substrate (WeasyPrint renderer)
and does not depend on this package at runtime.
"""
