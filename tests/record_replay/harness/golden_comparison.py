"""Golden file comparison framework for device tests.

Automatically captures markdown outputs from device tests and compares them
against golden files. Supports both online and offline testing modes.

Multi-Phase Support:
    - Goldens are stored in testdata: tests/testdata/{test_id}/goldens/
    - Compare specific phases: golden.compare(output, phase_name="final")
    - Compare vault directories: golden.compare_vault(vault_dir)

Usage:
    After a device test completes, capture the output:
        golden = GoldenComparison(test_id, goldens_dir, testdata_store)
        result = golden.compare(workspace.test_doc, phase_name="final")

    If output doesn't match golden:
        - Diff is displayed
        - Actual output saved to testdata goldens
        - User can approve with output path shown in error

    On first run (no golden exists):
        - Actual output saved
        - User can capture as golden
"""

import difflib
from pathlib import Path
from typing import NamedTuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .testdata import TestdataStore


class ComparisonResult(NamedTuple):
    """Result of golden file comparison."""

    matches: bool
    is_first_run: bool
    golden_file: Path
    actual_file: Path
    golden_content: str | None
    actual_content: str
    diff_lines: list[str]


class GoldenComparison:
    """Compare test outputs against golden files (phase-aware)."""

    def __init__(
        self,
        test_id: str,
        goldens_dir: Path,
        testdata_store: Optional["TestdataStore"] = None,
    ) -> None:
        """Initialize golden comparison.

        Args:
            test_id: Unique test identifier
            goldens_dir: Legacy goldens directory (for backward compat)
            testdata_store: TestdataStore for testdata-colocated goldens
        """
        self.test_id = test_id
        self.goldens_dir = Path(goldens_dir)
        self.testdata_store = testdata_store
        # Legacy file paths
        self.golden_file = self.goldens_dir / f"{test_id}.md"
        self.actual_file = self.goldens_dir / f"{test_id}.actual"

    def compare(
        self, output_file: Path, phase_name: str = "final"
    ) -> ComparisonResult:
        """Compare output file against golden (phase-aware).

        Checks testdata-colocated goldens first, falls back to legacy location.

        Args:
            output_file: Path to the actual output file to compare
            phase_name: Phase name for golden lookup (default: "final")

        Returns:
            ComparisonResult with comparison details
        """
        # Determine golden file location
        golden_file: Path | None = None
        actual_file: Path | None = None

        # Try testdata-colocated golden first
        if self.testdata_store:
            try:
                golden_vault = self.testdata_store.load_golden_vault(
                    self.test_id, phase_name
                )
                golden_file = golden_vault / output_file.name
                actual_file = golden_vault / f"{output_file.name}.actual"
            except FileNotFoundError:
                pass

        # Fall back to legacy location if not found
        if golden_file is None:
            golden_file = self.golden_file
            actual_file = self.actual_file

        # Ensure directories exist
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        actual_file.parent.mkdir(parents=True, exist_ok=True)

        actual_content = output_file.read_text()
        actual_file.write_text(actual_content)

        # First run - no golden exists yet
        if not golden_file.exists():
            return ComparisonResult(
                matches=True,  # Don't fail on first run
                is_first_run=True,
                golden_file=golden_file,
                actual_file=actual_file,
                golden_content=None,
                actual_content=actual_content,
                diff_lines=[],
            )

        # Compare against existing golden
        golden_content = golden_file.read_text()
        if actual_content == golden_content:
            return ComparisonResult(
                matches=True,
                is_first_run=False,
                golden_file=golden_file,
                actual_file=actual_file,
                golden_content=golden_content,
                actual_content=actual_content,
                diff_lines=[],
            )

        # Mismatch - generate diff
        diff_lines = list(
            difflib.unified_diff(
                golden_content.splitlines(keepends=True),
                actual_content.splitlines(keepends=True),
                fromfile=f"golden/{golden_file.name}",
                tofile="actual",
                lineterm="",
            )
        )

        return ComparisonResult(
            matches=False,
            is_first_run=False,
            golden_file=golden_file,
            actual_file=actual_file,
            golden_content=golden_content,
            actual_content=actual_content,
            diff_lines=diff_lines,
        )

    def compare_vault(
        self, vault_dir: Path, phase_name: str = "final"
    ) -> dict[str, ComparisonResult]:
        """Compare entire vault directory against golden.

        Compares all markdown files in the vault against golden versions.

        Args:
            vault_dir: Path to vault directory to compare
            phase_name: Phase name for golden lookup (default: "final")

        Returns:
            Dict mapping relative file paths to ComparisonResult objects
        """
        results: dict[str, ComparisonResult] = {}

        # Get golden vault directory
        golden_vault: Path | None = None
        if self.testdata_store:
            try:
                golden_vault = self.testdata_store.load_golden_vault(
                    self.test_id, phase_name
                )
            except FileNotFoundError:
                pass

        # Compare all markdown files
        if vault_dir.exists():
            for md_file in vault_dir.rglob("*.md"):
                rel_path = md_file.relative_to(vault_dir)

                if golden_vault and (golden_vault / rel_path).exists():
                    # Compare against golden
                    golden_content = (golden_vault / rel_path).read_text()
                    actual_content = md_file.read_text()
                    matches = actual_content == golden_content

                    if matches:
                        diff_lines = []
                    else:
                        diff_lines = list(
                            difflib.unified_diff(
                                golden_content.splitlines(keepends=True),
                                actual_content.splitlines(keepends=True),
                                fromfile=f"golden/{rel_path}",
                                tofile=str(rel_path),
                                lineterm="",
                            )
                        )

                    results[str(rel_path)] = ComparisonResult(
                        matches=matches,
                        is_first_run=False,
                        golden_file=golden_vault / rel_path,
                        actual_file=vault_dir / rel_path,
                        golden_content=golden_content if not matches else None,
                        actual_content=actual_content,
                        diff_lines=diff_lines,
                    )
                else:
                    # No golden exists yet
                    actual_content = md_file.read_text()
                    results[str(rel_path)] = ComparisonResult(
                        matches=True,  # Don't fail on first run
                        is_first_run=True,
                        golden_file=Path(
                            str(md_file).replace(str(vault_dir), "golden")
                        ),
                        actual_file=md_file,
                        golden_content=None,
                        actual_content=actual_content,
                        diff_lines=[],
                    )

        return results

    def print_result(self, result: ComparisonResult, verbose: bool = True) -> None:
        """Print comparison result to console.

        Args:
            result: ComparisonResult from compare()
            verbose: If True, show diff and full output
        """
        if result.matches:
            if result.is_first_run:
                print(f"\n{'='*70}")
                print(f"NO GOLDEN FILE (First run): {self.golden_file.name}")
                print(f"{'='*70}")
                print("\nTo capture this output as golden, run:")
                print(f"  cp {self.actual_file} {self.golden_file}")
                if verbose:
                    print(f"\nOutput ({len(result.actual_content)} chars):")
                    print("-" * 70)
                    print(result.actual_content[:500])
                    if len(result.actual_content) > 500:
                        print(f"... ({len(result.actual_content) - 500} more chars)")
            else:
                print(f"✓ {self.golden_file.name} matches golden")
        else:
            print(f"\n{'='*70}")
            print(f"OUTPUT MISMATCH: {self.golden_file.name}")
            print(f"{'='*70}")

            if verbose and result.diff_lines:
                print("\nDiff (first 50 lines):")
                print("".join(result.diff_lines[:50]))
                if len(result.diff_lines) > 50:
                    print(f"\n... and {len(result.diff_lines) - 50} more lines")

            print(f"\nTo approve new output, run:")
            print(f"  cp {self.actual_file} {self.golden_file}")
