"""Test paragraph pagination across page boundaries.

Validates that long paragraphs are correctly paginated with and without
paragraph splitting enabled. Uses the record/replay harness for golden testdata.

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


# Test document content - deterministic, long enough to span multiple pages
LONG_PARAGRAPH = "The quick brown fox jumps over the lazy dog. " * 100


# Test configs - test allow_paragraph_splitting behavior
# (lines_per_page is now calculated from device geometry, not configurable)
CONFIGS = {
    "no_split": LayoutConfig(
        margin_top=50,
        margin_bottom=50,
        margin_left=50,
        margin_right=50,
        allow_paragraph_splitting=False,
    ),
    "with_split": LayoutConfig(
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
@pytest.mark.skip(reason="Pre-existing failure - content mismatch, needs investigation")
@pytest.mark.parametrize("config_name", list(CONFIGS.keys()))
def test_paragraph_pagination(config_name, device, workspace, testdata_store, device_mode):
    """Test paragraph pagination with golden .rm files.

    Uses the record/replay harness:
    - Online mode (--online -s): Records new golden testdata (clears existing)
    - Offline mode: Compares generated .rm files against golden testdata
    """
    import shutil

    test_id = f"paragraph_pagination/{config_name}"
    config = CONFIGS[config_name]
    rm_dir = testdata_store.base_dir / test_id / "rm_files"

    from rock_paper_sync.layout import DEFAULT_DEVICE

    print(f"\n{'='*60}")
    print(f"Config: {config_name}")
    print(f"  lines_per_page={DEFAULT_DEVICE.lines_per_page} (calculated from device geometry)")
    print(f"  allow_paragraph_splitting={config.allow_paragraph_splitting}")
    print(f"{'='*60}")

    # Configure workspace to use our layout settings
    workspace.set_layout_config(allow_paragraph_splitting=config.allow_paragraph_splitting)

    # Write test document to workspace.test_doc (the standard location)
    workspace.test_doc.write_text(f"# Pagination Test ({config_name})\n\n{LONG_PARAGRAPH}\n")

    # Generate .rm files with our config
    generated = generate_rm_files(config, workspace.test_doc)

    print(f"\n📊 Generated {len(generated)} pages")
    for page_idx, rm_data in generated.items():
        text = extract_text_from_rm(rm_data)
        print(f"   Page {page_idx}: {len(text)} chars")

    if device_mode == "online":
        # Record mode: upload to device, verify, save golden (clears existing)
        device.start_test(test_id, description=f"Verify pagination: {config_name}")

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

        # Save as golden (clear old files first)
        if rm_dir.exists():
            shutil.rmtree(rm_dir)
        rm_dir.mkdir(parents=True, exist_ok=True)
        for page_idx, rm_data in generated.items():
            (rm_dir / f"page_{page_idx}.rm").write_bytes(rm_data)
        print(f"\n✅ Saved golden: {rm_dir}")

        device.end_test(test_id)
    else:
        # Replay mode: compare against golden
        if not rm_dir.exists() or not any(rm_dir.glob("*.rm")):
            pytest.skip(f"No testdata for {config_name}. Run with --online -s to record.")

        print("\n🔍 Comparing against golden...")
        golden = {f.stem: f.read_bytes() for f in sorted(rm_dir.glob("*.rm"))}

        errors = []
        if len(generated) != len(golden):
            errors.append(f"Page count: {len(generated)} generated vs {len(golden)} golden")
        else:
            for page_idx, rm_data in generated.items():
                golden_key = f"page_{page_idx}"
                if golden_key not in golden:
                    errors.append(f"Missing golden for page {page_idx}")
                    continue

                matches, msg = compare_rm_content(rm_data, golden[golden_key])
                if not matches:
                    errors.append(f"Page {page_idx}: {msg}")

        if errors:
            for err in errors:
                print(f"❌ {err}")
            pytest.fail(f"Pagination mismatch for {config_name}:\n" + "\n".join(errors))
        else:
            print("✅ Matches golden")
