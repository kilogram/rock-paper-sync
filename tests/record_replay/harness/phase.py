"""Phase management utilities for record/replay tests.

Provides clean abstractions over the begin_phase/end_phase pattern, typed
annotation state snapshots, and debug-output helpers.

Example - PhaseContext replaces the if/else begin_phase pattern:

    anno_state: AnnotationState | None = None
    with PhaseContext(device, 1, "initial_upload") as ctx:
        if ctx.should_run:
            state = device.wait_for_annotations(doc_uuid)
            anno_state = AnnotationState.from_document_state(state)
    anno_state = anno_state or ctx.restored_state

    with PhaseContext(device, 2, "resync") as ctx:
        if ctx.should_run:
            device.trigger_sync()
            new_state = AnnotationState.from_document_state(device.get_document_state(doc_uuid))
            anno_state.assert_count_preserved(new_state)
            anno_state = new_state
    anno_state = anno_state or ctx.restored_state

When resuming (--resume-from-phase=2), phase 1's begin_phase returns False,
ctx.should_run is False, and ctx.restored_state provides the AnnotationState
from the saved checkpoint — no else branch needed.

In offline replay, begin_phase always returns True, so ctx.should_run is
always True and ctx.restored_state is always None.
"""

from __future__ import annotations

import io
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from rock_paper_sync.annotations import AnnotationType, read_annotations

if TYPE_CHECKING:
    from .protocol import DeviceInteractionProtocol, DocumentState


def extract_annotations(
    rm_files: dict[str, bytes],
) -> tuple[list[Any], list[Any]]:
    """Extract and categorize annotations from .rm files.

    Replaces the repeated pattern in every test:
        all_annotations = []
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            all_annotations.extend(annotations)
        highlights = [a for a in all_annotations if a.type == AnnotationType.HIGHLIGHT]
        strokes = [a for a in all_annotations if a.type == AnnotationType.STROKE]

    Args:
        rm_files: Mapping of page_uuid -> .rm file bytes

    Returns:
        (highlights, strokes) tuple of annotation lists
    """
    highlights = []
    strokes = []
    for rm_data in rm_files.values():
        for annotation in read_annotations(io.BytesIO(rm_data)):
            if annotation.type == AnnotationType.HIGHLIGHT:
                highlights.append(annotation)
            elif annotation.type == AnnotationType.STROKE:
                strokes.append(annotation)
    return highlights, strokes


@dataclass
class AnnotationState:
    """Typed snapshot of annotation data at a point in test execution.

    Carries all state needed to cross phase boundaries: rm_files for
    comparison utilities, categorized annotations for count assertions,
    and document identity for reloading.
    """

    rm_files: dict[str, bytes]
    doc_uuid: str
    page_uuids: list[str] = field(default_factory=list)
    highlights: list[Any] = field(default_factory=list)
    strokes: list[Any] = field(default_factory=list)

    @classmethod
    def from_document_state(cls, state: DocumentState) -> AnnotationState:
        """Create from a DocumentState returned by device methods."""
        highlights, strokes = extract_annotations(state.rm_files)
        return cls(
            rm_files=state.rm_files,
            doc_uuid=state.doc_uuid,
            page_uuids=list(state.page_uuids),
            highlights=highlights,
            strokes=strokes,
        )

    @classmethod
    def from_recording_phase(cls, phase_dict: dict) -> AnnotationState | None:
        """Restore from a saved recording phase checkpoint.

        Args:
            phase_dict: Dict returned by testdata_store.load_recording_phase()

        Returns:
            AnnotationState if the checkpoint has rm_files, None otherwise
        """
        rm_files = phase_dict.get("rm_files", {})
        if not rm_files:
            return None
        highlights, strokes = extract_annotations(rm_files)
        return cls(
            rm_files=rm_files,
            doc_uuid=phase_dict.get("doc_uuid") or "",
            page_uuids=list(phase_dict.get("page_uuids", [])),
            highlights=highlights,
            strokes=strokes,
        )

    def assert_count_preserved(
        self,
        after: AnnotationState,
        check: Literal["highlights", "strokes", "both"] = "both",
        msg: str = "",
    ) -> None:
        """Assert annotation counts are preserved from self (before) to after.

        Args:
            after: State to compare against (the "after" snapshot)
            check: Which annotation types to check
            msg: Extra context appended to failure message
        """
        suffix = f" — {msg}" if msg else ""
        if check in ("highlights", "both") and self.highlights:
            assert len(after.highlights) == len(
                self.highlights
            ), f"Highlights lost: {len(self.highlights)} → {len(after.highlights)}{suffix}"
        if check in ("strokes", "both") and self.strokes:
            assert len(after.strokes) == len(
                self.strokes
            ), f"Strokes lost: {len(self.strokes)} → {len(after.strokes)}{suffix}"

    def assert_positions_match(
        self,
        after: AnnotationState,
        tolerance_px: float = 5.0,
    ) -> None:
        """Assert highlight positions are preserved within tolerance.

        Delegates to comparison.assert_highlights_match().

        Args:
            after: State whose rm_files to compare against self (the golden)
            tolerance_px: Maximum allowed position difference in pixels
        """
        from .comparison import assert_highlights_match

        assert_highlights_match(after.rm_files, self.rm_files, tolerance_px=tolerance_px)


class PhaseContext:
    """Context manager replacing the if/else begin_phase/end_phase pattern.

    In online mode with --resume-from-phase=N:
      - Phases before N: begin_phase returns False → ctx.should_run=False,
        ctx.restored_state is populated from the saved checkpoint.
      - Phase N and after: begin_phase returns True → ctx.should_run=True,
        ctx.restored_state=None.

    In offline replay mode:
      - begin_phase always returns True → ctx.should_run always True,
        ctx.restored_state always None. No special paths.

    Usage:
        anno_state: AnnotationState | None = None
        with PhaseContext(device, 1, "initial_upload") as ctx:
            if ctx.should_run:
                state = device.wait_for_annotations(doc_uuid)
                anno_state = AnnotationState.from_document_state(state)
        anno_state = anno_state or ctx.restored_state
    """

    def __init__(
        self,
        device: DeviceInteractionProtocol,
        phase_id: int,
        phase_name: str,
        description: str = "",
    ) -> None:
        self._device = device
        self._phase_id = phase_id
        self._phase_name = phase_name
        self._description = description
        self._should_run: bool = True
        self._restored_state: AnnotationState | None = None

    @property
    def should_run(self) -> bool:
        """True if the phase body should execute."""
        return self._should_run

    @property
    def restored_state(self) -> AnnotationState | None:
        """AnnotationState restored from checkpoint when phase was skipped.

        Non-None only when the phase was skipped during resumption AND
        the checkpoint contained rm_files.
        """
        return self._restored_state

    def __enter__(self) -> PhaseContext:
        self._should_run = self._device.begin_phase(
            self._phase_id, self._phase_name, self._description
        )
        if not self._should_run:
            self._restored_state = self._load_restored_state()
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        if self._should_run and exc_type is None:
            self._device.end_phase()
        return False  # never suppress exceptions

    def _load_restored_state(self) -> AnnotationState | None:
        """Attempt to load AnnotationState from the saved recording phase."""
        # Access testdata_store via duck typing (both Online and Offline have it)
        testdata_store = getattr(self._device, "testdata_store", None)
        test_id = getattr(self._device, "_current_test_id", None)
        if testdata_store is None or not test_id:
            return None
        phase_dict = testdata_store.load_recording_phase(test_id, self._phase_id)
        if phase_dict is None:
            return None
        return AnnotationState.from_recording_phase(phase_dict)


@contextmanager
def debug_on_failure(
    test_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    output_dir: Path,
    name: str = "comparison",
) -> Generator[None, None, None]:
    """Context manager: save debug images on AssertionError.

    Wraps any comparison that should leave behind diagnostic images when
    it fails. Images are saved to output_dir/name/.

    Args:
        test_rm_files: page_uuid -> .rm bytes from test output
        golden_rm_files: page_uuid -> .rm bytes from golden reference
        output_dir: Base directory for debug images
        name: Subdirectory name within output_dir

    Example:
        with debug_on_failure(test_rm, golden_rm, tmp_path / "debug", "phase4"):
            assert_highlights_match(test_rm, golden_rm)
    """
    try:
        yield
    except AssertionError:
        try:
            from .visual_comparison import save_comparison_debug_images

            saved = save_comparison_debug_images(test_rm_files, golden_rm_files, output_dir / name)
            if saved:
                print(
                    f"\n[debug_on_failure] Saved {len(saved)} debug image(s) to {output_dir / name}"
                )
        except Exception as e:
            print(f"\n[debug_on_failure] Could not save debug images: {e}")
        raise
