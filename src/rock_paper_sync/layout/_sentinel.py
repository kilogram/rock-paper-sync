"""Sentinel objects for the unavailable native layout engine.

Each sentinel allows import and attribute access (returning further sentinels)
but raises ``NativeLayoutUnavailableError`` on any real use: arithmetic,
comparison, calling, iteration, or boolean coercion.  This lets modules that
import layout names at the top level or derive constants from them load
without error, while any code path that actually *uses* the layout engine
fails with a clear typed error instead of a traceback.
"""

from __future__ import annotations

from rock_paper_sync.errors import NativeLayoutUnavailableError

_MESSAGE = (
    "Native layout ({name}) is unavailable: it has moved to native_layout/. "
    "Pull sync requires the PDF-substrate pull path (Phase 4); "
    "use --direction push."
)


class _LayoutSentinel:
    """Placeholder that raises on real use."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def _raise(self) -> None:
        raise NativeLayoutUnavailableError(_MESSAGE.format(name=self._name))

    # --- Attribute access returns more sentinels (for module-level constant derivation) ---

    def __getattr__(self, name: str) -> _LayoutSentinel:
        return _LayoutSentinel(f"{self._name}.{name}")

    def __setattr__(self, name: str, value: object) -> None:
        self._raise()

    # --- Calling / instantiation ---

    def __call__(self, *args: object, **kwargs: object) -> None:
        self._raise()

    # --- Arithmetic (covers module-level constant derivation that tries to compute) ---

    def __add__(self, other: object) -> None:
        self._raise()

    def __radd__(self, other: object) -> None:
        self._raise()

    def __sub__(self, other: object) -> None:
        self._raise()

    def __rsub__(self, other: object) -> None:
        self._raise()

    def __mul__(self, other: object) -> None:
        self._raise()

    def __rmul__(self, other: object) -> None:
        self._raise()

    def __truediv__(self, other: object) -> None:
        self._raise()

    def __rtruediv__(self, other: object) -> None:
        self._raise()

    def __floordiv__(self, other: object) -> None:
        self._raise()

    def __rfloordiv__(self, other: object) -> None:
        self._raise()

    # --- Comparison ---

    def __eq__(self, other: object) -> None:  # type: ignore[override]
        self._raise()

    def __ne__(self, other: object) -> None:  # type: ignore[override]
        self._raise()

    def __lt__(self, other: object) -> None:
        self._raise()

    def __le__(self, other: object) -> None:
        self._raise()

    def __gt__(self, other: object) -> None:
        self._raise()

    def __ge__(self, other: object) -> None:
        self._raise()

    # --- Conversion / coercion ---

    def __bool__(self) -> None:  # type: ignore[override]
        self._raise()

    def __int__(self) -> None:  # type: ignore[override]
        self._raise()

    def __float__(self) -> None:  # type: ignore[override]
        self._raise()

    def __str__(self) -> str:
        return f"<unavailable: {self._name}>"

    def __repr__(self) -> str:
        return f"_LayoutSentinel({self._name!r})"

    # --- Bitwise / union (supports `Sentinel | None` in type annotations) ---

    def __or__(self, other: object) -> _LayoutSentinel:
        return self

    def __ror__(self, other: object) -> _LayoutSentinel:
        return self

    # --- Iteration / container ---

    def __iter__(self) -> None:  # type: ignore[override]
        self._raise()

    def __len__(self) -> None:  # type: ignore[override]
        self._raise()

    def __hash__(self) -> int:
        return id(self)


def _make_sentinel(name: str) -> _LayoutSentinel:
    """Create a named sentinel for a layout export."""
    return _LayoutSentinel(name)
