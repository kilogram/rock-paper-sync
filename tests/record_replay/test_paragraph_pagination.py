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
    """Compare .rm files by their text content."""
    gen_text = extract_text_from_rm(generated)
    gold_text = extract_text_from_rm(golden)

    if gen_text == gold_text:
        return True, "Content matches"

    if len(gen_text) != len(gold_text):
        return False, f"Length mismatch: {len(gen_text)} vs {len(gold_text)} chars"

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


class PaginationGoldens:
    """Manages golden .rm files for pagination tests."""

    def __init__(self, testdata_dir: Path):
        self.testdata_dir = testdata_dir

    def rm_files_dir(self, config_name: str) -> Path:
        return self.testdata_dir / config_name / "rm_files"

    def has_golden(self, config_name: str) -> bool:
        rm_dir = self.rm_files_dir(config_name)
        return rm_dir.exists() and any(rm_dir.glob("*.rm"))

    def save(self, config_name: str, rm_files: dict[int, bytes]) -> None:
        """Save .rm files as golden."""
        rm_dir = self.rm_files_dir(config_name)
        rm_dir.mkdir(parents=True, exist_ok=True)
        for page_idx, rm_data in rm_files.items():
            (rm_dir / f"page_{page_idx}.rm").write_bytes(rm_data)

    def load(self, config_name: str) -> dict[str, bytes]:
        """Load golden .rm files."""
        rm_dir = self.rm_files_dir(config_name)
        return {f.stem: f.read_bytes() for f in sorted(rm_dir.glob("*.rm"))}

    def compare(self, config_name: str, generated: dict[int, bytes]) -> tuple[bool, list[str]]:
        """Compare generated .rm files against golden."""
        golden = self.load(config_name)
        errors = []

        if len(generated) != len(golden):
            errors.append(f"Page count: {len(generated)} generated vs {len(golden)} golden")
            return False, errors

        for page_idx, rm_data in generated.items():
            golden_key = f"page_{page_idx}"
            if golden_key not in golden:
                errors.append(f"Missing golden for page {page_idx}")
                continue

            matches, msg = compare_rm_content(rm_data, golden[golden_key])
            if not matches:
                errors.append(f"Page {page_idx}: {msg}")

        return len(errors) == 0, errors


# Test document content - deterministic
LONG_PARAGRAPH = "The quick brown fox jumps over the lazy dog. " * 50


# Test configs - use default margins
CONFIGS = {
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


def generate_rm_files(config: LayoutConfig, md_path: Path) -> dict[int, bytes]:
    """Generate .rm files for a document with given config."""
    doc = parse_markdown_file(md_path)
    generator = RemarkableGenerator(config)
    result = generator.generate_document(doc)

    rm_files = {}
    for i, page in enumerate(result.pages):
        rm_files[i] = generator.generate_rm_file(page)
    return rm_files


@pytest.mark.device
@pytest.mark.parametrize("config_name", list(CONFIGS.keys()))
def test_paragraph_pagination(config_name, device, workspace, fixtures_dir):
    """Test paragraph pagination with golden .rm files.

    Record mode (--online -s):
    1. Generate document with config
    2. Upload to device for visual verification
    3. User approves pagination looks correct
    4. Save .rm files as golden

    Replay mode:
    1. Generate document
    2. Compare against golden .rm files
    """
    testdata_dir = fixtures_dir.parent / "testdata" / "paragraph_pagination"
    goldens = PaginationGoldens(testdata_dir)
    config = CONFIGS[config_name]

    print(f"\n{'='*60}")
    print(f"Config: {config_name}")
    print(f"  lines_per_page={config.lines_per_page}")
    print(f"  allow_paragraph_splitting={config.allow_paragraph_splitting}")
    print(f"{'='*60}")

    # Write test document to workspace.test_doc (the standard location)
    workspace.test_doc.write_text(f"# Pagination Test ({config_name})\n\n{LONG_PARAGRAPH}\n")

    # Generate .rm files with our config
    generated = generate_rm_files(config, workspace.test_doc)

    print(f"\n📊 Generated {len(generated)} pages")
    for page_idx, rm_data in generated.items():
        text = extract_text_from_rm(rm_data)
        print(f"   Page {page_idx}: {len(text)} chars")

    if goldens.has_golden(config_name):
        # Replay: compare against golden
        print("\n🔍 Comparing against golden...")
        matches, errors = goldens.compare(config_name, generated)

        if matches:
            print("✅ Matches golden")
        else:
            for err in errors:
                print(f"❌ {err}")
            pytest.fail(f"Pagination mismatch for {config_name}:\n" + "\n".join(errors))
    else:
        # Record: upload to device, verify, save golden
        try:
            device.start_test(
                f"pagination_{config_name}",
                description=f"Verify pagination: {config_name}",
            )
        except FileNotFoundError:
            pytest.skip(f"No testdata for {config_name}. Run with --online -s to record.")

        print("\n📤 Uploading to device...")
        device.upload_document(workspace.test_doc)

        print("\n👀 Verify on device:")
        print(f"   - Document should have {len(generated)} pages")
        if config.allow_paragraph_splitting:
            print("   - Paragraphs may be split at page boundaries")
        else:
            print("   - Paragraphs should stay together (but oversized ones must split)")

        device.observe_result(
            f"Config: {config_name}\n"
            f"Expected: {len(generated)} pages\n"
            f"Does the pagination look correct?"
        )

        # Save as golden
        goldens.save(config_name, generated)
        print(f"\n✅ Saved golden: {goldens.rm_files_dir(config_name)}")

        device.end_test(f"pagination_{config_name}")
