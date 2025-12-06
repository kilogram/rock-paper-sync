"""Test paragraph pagination across page boundaries.

Validates that long paragraphs are correctly paginated with and without
paragraph splitting enabled. Stores generated .rm files as golden testdata.

Recording (generate, verify on device, save as golden):
    uv run pytest tests/record_replay/test_paragraph_pagination.py --online -s

Replaying (compare generated .rm against golden):
    uv run pytest tests/record_replay/test_paragraph_pagination.py
"""

import io
from pathlib import Path

import pytest
import rmscene

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.generator import RemarkableGenerator
from rock_paper_sync.parser import parse_markdown_file


def extract_text_from_rm(rm_data: bytes) -> str:
    """Extract text content from .rm file."""
    text_parts = []
    for block in rmscene.read_blocks(io.BytesIO(rm_data)):
        if "RootText" in type(block).__name__:
            text_data = block.value
            for item in text_data.items.sequence_items():
                if hasattr(item, "value") and isinstance(item.value, str):
                    text_parts.append(item.value)
    return "".join(text_parts)


def compare_rm_content(generated: bytes, golden: bytes) -> tuple[bool, str]:
    """Compare .rm files by their text content.

    Binary comparison won't work due to non-deterministic UUIDs/timestamps.
    Instead, we compare the extracted text content.
    """
    gen_text = extract_text_from_rm(generated)
    gold_text = extract_text_from_rm(golden)

    if gen_text == gold_text:
        return True, "Content matches"

    # Find differences
    if len(gen_text) != len(gold_text):
        return False, f"Length mismatch: {len(gen_text)} vs {len(gold_text)} chars"

    # Find first difference
    for i, (g, o) in enumerate(zip(gen_text, gold_text)):
        if g != o:
            context_start = max(0, i - 20)
            context_end = min(len(gen_text), i + 20)
            return False, (
                f"Content differs at position {i}:\n"
                f"  generated: ...{gen_text[context_start:context_end]}...\n"
                f"  golden:    ...{gold_text[context_start:context_end]}..."
            )

    return False, "Unknown difference"


class PaginationTestdata:
    """Manages testdata for pagination tests."""

    def __init__(self, testdata_dir: Path):
        self.testdata_dir = testdata_dir

    def config_dir(self, config_name: str) -> Path:
        return self.testdata_dir / config_name

    def rm_files_dir(self, config_name: str) -> Path:
        return self.config_dir(config_name) / "rm_files"

    def has_golden(self, config_name: str) -> bool:
        rm_dir = self.rm_files_dir(config_name)
        return rm_dir.exists() and any(rm_dir.glob("*.rm"))

    def save_golden(self, config_name: str, rm_files: dict[str, bytes]) -> None:
        """Save generated .rm files as golden."""
        rm_dir = self.rm_files_dir(config_name)
        rm_dir.mkdir(parents=True, exist_ok=True)

        for page_idx, rm_data in rm_files.items():
            (rm_dir / f"page_{page_idx}.rm").write_bytes(rm_data)

    def load_golden(self, config_name: str) -> dict[str, bytes]:
        """Load golden .rm files."""
        rm_dir = self.rm_files_dir(config_name)
        rm_files = {}
        for rm_file in sorted(rm_dir.glob("*.rm")):
            rm_files[rm_file.stem] = rm_file.read_bytes()
        return rm_files

    def compare(self, config_name: str, generated: dict[str, bytes]) -> tuple[bool, list[str]]:
        """Compare generated .rm files against golden.

        Returns (all_match, list_of_error_messages)
        """
        golden = self.load_golden(config_name)
        errors = []

        # Check page count
        if len(generated) != len(golden):
            errors.append(
                f"Page count mismatch: {len(generated)} generated vs {len(golden)} golden"
            )
            return False, errors

        # Compare each page
        for page_key in sorted(generated.keys()):
            golden_key = f"page_{page_key}" if not page_key.startswith("page_") else page_key

            if golden_key not in golden:
                errors.append(f"Missing golden for {page_key}")
                continue

            matches, msg = compare_rm_content(generated[page_key], golden[golden_key])
            if not matches:
                errors.append(f"Page {page_key}: {msg}")

        return len(errors) == 0, errors


@pytest.mark.device
def test_paragraph_pagination(device, workspace, fixtures_dir):
    """Test paragraph pagination with golden .rm files.

    Record mode (--online -s):
    1. Generate documents with various pagination configs
    2. Upload to device for visual verification
    3. User approves pagination looks correct
    4. Save .rm files as golden testdata

    Replay mode:
    1. Generate documents with same configs
    2. Compare generated .rm content against golden
    3. Fail if content differs
    """
    testdata_dir = fixtures_dir.parent / "testdata" / "paragraph_pagination"
    testdata = PaginationTestdata(testdata_dir)

    # Test configurations
    configs = {
        "short_no_split": LayoutConfig(
            lines_per_page=10,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
            allow_paragraph_splitting=False,
        ),
        "short_with_split": LayoutConfig(
            lines_per_page=10,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
            allow_paragraph_splitting=True,
        ),
        "normal_no_split": LayoutConfig(
            lines_per_page=26,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
            allow_paragraph_splitting=False,
        ),
        "normal_with_split": LayoutConfig(
            lines_per_page=26,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
            allow_paragraph_splitting=True,
        ),
    }

    # Test document content - deterministic
    words = "The quick brown fox jumps over the lazy dog. " * 50

    # Check if we have any goldens (determines record vs replay mode)
    has_any_golden = any(testdata.has_golden(name) for name in configs)

    if not has_any_golden:
        # Record mode - need device
        try:
            test_id = device.start_test(
                "paragraph_pagination",
                description="Verify paragraph pagination with various configs",
            )
        except FileNotFoundError:
            pytest.skip("No testdata. Run with --online -s to record.")

    results = {}
    all_errors = []

    for config_name, config in configs.items():
        print(f"\n{'='*60}")
        print(f"Config: {config_name}")
        print(f"  lines_per_page={config.lines_per_page}, split={config.allow_paragraph_splitting}")
        print(f"{'='*60}")

        # Create test document
        md_file = workspace.workspace_dir / f"pagination_{config_name}.md"
        md_file.write_text(f"# Pagination Test ({config_name})\n\n{words}\n")
        doc = parse_markdown_file(md_file)

        # Generate .rm files
        generator = RemarkableGenerator(config)
        result = generator.generate_document(doc)

        generated_rm = {}
        for i, page in enumerate(result.pages):
            rm_data = generator.generate_rm_file(page)
            generated_rm[str(i)] = rm_data

        print(f"\n📊 Generated {len(generated_rm)} pages")
        for page_idx, rm_data in generated_rm.items():
            text = extract_text_from_rm(rm_data)
            print(f"   Page {page_idx}: {len(text)} chars")
            print(f"      Preview: {text[:60]}...")

        if testdata.has_golden(config_name):
            # Replay mode - compare against golden
            print("\n🔍 Comparing against golden...")
            matches, errors = testdata.compare(config_name, generated_rm)

            if matches:
                print("✅ Matches golden")
                results[config_name] = True
            else:
                for err in errors:
                    print(f"❌ {err}")
                    all_errors.append(f"{config_name}: {err}")
                results[config_name] = False
        else:
            # Record mode - upload and save golden
            print("\n📤 Uploading to device for verification...")

            device.upload_document(md_file)

            print("\n👀 Please verify on device:")
            print(f"   - Document should have {len(generated_rm)} pages")
            print("   - Text should flow correctly across pages")
            if config.allow_paragraph_splitting:
                print("   - Paragraphs MAY be split at page boundaries")
            else:
                print("   - Paragraphs should NOT be split")

            device.observe_result(
                f"Verify pagination for {config_name}:\n"
                f"  {len(generated_rm)} pages expected\n"
                f"  Does the pagination look correct?"
            )

            # Save as golden
            testdata.save_golden(config_name, generated_rm)
            print(f"\n✅ Saved golden to {testdata.rm_files_dir(config_name)}")
            results[config_name] = True

    # Summary
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")

    all_passed = True
    for config_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {config_name}: {status}")
        if not passed:
            all_passed = False

    if not has_any_golden:
        device.end_test(test_id)

    if all_errors:
        print("\nErrors:")
        for err in all_errors:
            print(f"  - {err}")

    assert all_passed, "Pagination tests failed:\n" + "\n".join(all_errors)
