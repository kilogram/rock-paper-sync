"""Golden file comparison framework for device tests.

Automatically captures markdown outputs from device tests and compares them
against golden files. Supports both online and offline testing modes.

Usage:
    After a device test completes, capture the output:
        golden = GoldenComparison(test_id, goldens_dir)
        result = golden.compare(workspace.test_doc)

    If output doesn't match golden:
        - Diff is displayed
        - Actual output saved to {test_id}.actual
        - User can approve with: cp {test_id}.actual {test_id}.md

    On first run (no golden exists):
        - Actual output saved
        - User can capture as golden with: cp {test_id}.actual {test_id}.md
"""

import difflib
from pathlib import Path
from typing import NamedTuple


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
    """Compare test outputs against golden files."""

    def __init__(self, test_id: str, goldens_dir: Path) -> None:
        """Initialize golden comparison.

        Args:
            test_id: Unique test identifier (used for filename)
            goldens_dir: Directory containing golden files
        """
        self.test_id = test_id
        self.goldens_dir = Path(goldens_dir)
        self.golden_file = self.goldens_dir / f"{test_id}.md"
        self.actual_file = self.goldens_dir / f"{test_id}.actual"

    def compare(self, output_file: Path) -> ComparisonResult:
        """Compare output file against golden.

        Args:
            output_file: Path to the actual output file to compare

        Returns:
            ComparisonResult with comparison details
        """
        self.goldens_dir.mkdir(parents=True, exist_ok=True)

        actual_content = output_file.read_text()
        self.actual_file.write_text(actual_content)

        # First run - no golden exists yet
        if not self.golden_file.exists():
            return ComparisonResult(
                matches=True,  # Don't fail on first run
                is_first_run=True,
                golden_file=self.golden_file,
                actual_file=self.actual_file,
                golden_content=None,
                actual_content=actual_content,
                diff_lines=[]
            )

        # Compare against existing golden
        golden_content = self.golden_file.read_text()
        if actual_content == golden_content:
            return ComparisonResult(
                matches=True,
                is_first_run=False,
                golden_file=self.golden_file,
                actual_file=self.actual_file,
                golden_content=golden_content,
                actual_content=actual_content,
                diff_lines=[]
            )

        # Mismatch - generate diff
        diff_lines = list(difflib.unified_diff(
            golden_content.splitlines(keepends=True),
            actual_content.splitlines(keepends=True),
            fromfile=f"golden/{self.golden_file.name}",
            tofile="actual",
            lineterm=""
        ))

        return ComparisonResult(
            matches=False,
            is_first_run=False,
            golden_file=self.golden_file,
            actual_file=self.actual_file,
            golden_content=golden_content,
            actual_content=actual_content,
            diff_lines=diff_lines
        )

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
