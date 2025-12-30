"""CRDT format encoding/decoding for reMarkable v6 files.

This module handles varint encoding/decoding and CRDT ID manipulation for
highlight text anchoring in reMarkable firmware 3.6+.

In firmware 3.6+, highlights store their text anchor position in extra_value_data
as a CrdtId. The format is:
  Field 15 (tag 0x7F): Start CrdtId (m_firstId) - first char of highlight
  Field 17 (tag 0x8F): Fixed prefix (0x01 0x01) + end position varint (m_lastId)

Where base_id comes from the RootTextBlock's CrdtSequenceItem.item_id.part2.
This allows updating highlight positions when text shifts.
"""

import logging
from pathlib import Path

import rmscene

logger = logging.getLogger(__name__)


# =============================================================================
# CRDT Field Tags
# =============================================================================


class CrdtFieldTags:
    """CRDT extra_value_data field tags for highlights."""

    FIELD_15_START_ANCHOR = 0x7F  # m_firstId - start position CrdtId
    FIELD_17_END_POSITION = 0x8F  # m_lastId - end position with fixed prefix


# =============================================================================
# Varint Encoding/Decoding
# =============================================================================


def decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint starting at pos.

    Args:
        data: Byte array containing varint
        pos: Starting position

    Returns:
        Tuple of (decoded value, position after varint)
    """
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def encode_varint(value: int) -> bytes:
    """Encode an integer as a varint.

    Args:
        value: Integer to encode

    Returns:
        Varint-encoded bytes
    """
    result = []
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def decode_crdt_id(data: bytes, pos: int) -> tuple[tuple[int, int], int]:
    """Decode a CrdtId (two varints) starting at pos.

    Args:
        data: Byte array containing CrdtId
        pos: Starting position

    Returns:
        Tuple of ((part1, part2), position after CrdtId)
    """
    part1, pos = decode_varint(data, pos)
    part2, pos = decode_varint(data, pos)
    return (part1, part2), pos


def encode_crdt_id(part1: int, part2: int) -> bytes:
    """Encode a CrdtId as two varints.

    Args:
        part1: First part of CrdtId (author_id)
        part2: Second part of CrdtId (position)

    Returns:
        CrdtId encoded as bytes
    """
    return encode_varint(part1) + encode_varint(part2)


# =============================================================================
# Highlight Anchor Manipulation
# =============================================================================


def update_glyph_extra_value_data(
    extra_data: bytes, new_char_offset: int, highlight_length: int, crdt_base_id: int = 16
) -> bytes:
    """Update the character offset anchors in extra_value_data.

    The extra_value_data contains tagged fields for text anchoring:
    - Field 15 (tag 0x7F): Start CrdtId (m_firstId) - first char of highlight
    - Field 17 (tag 0x8F): Fixed prefix (0x01 0x01) + end position varint (m_lastId)

    The device reads m_firstId from Field 15 and m_lastId end position from
    the varint after the fixed prefix in Field 17. Both must be updated.

    Format discovered from device firmware 3.6+ behavior:
    - 7f [author_varint] [start_pos_varint]
    - 8f 01 01 [end_pos_varint]
    - [remaining fields...]

    Args:
        extra_data: Original extra_value_data bytes
        new_char_offset: New character offset (start) in the text
        highlight_length: Length of the highlighted text
        crdt_base_id: Base ID from RootTextBlock (usually 16)

    Returns:
        Updated extra_value_data with new start and end positions
    """
    if len(extra_data) < 3:
        logger.debug("extra_value_data too short to contain anchor CrdtId")
        return extra_data

    # Verify this is Field 15 with CrdtId type (tag 0x7F)
    if extra_data[0] != CrdtFieldTags.FIELD_15_START_ANCHOR:
        logger.debug(f"Expected tag 0x7F, got 0x{extra_data[0]:02x}")
        return extra_data

    # Decode Field 15: Start CrdtId (m_firstId)
    old_start_crdt, pos_after_field15 = decode_crdt_id(extra_data, 1)
    author_id = old_start_crdt[0]

    # Check for Field 17 (tag 0x8F)
    if (
        pos_after_field15 >= len(extra_data)
        or extra_data[pos_after_field15] != CrdtFieldTags.FIELD_17_END_POSITION
    ):
        logger.debug(
            f"Expected tag 0x8F at pos {pos_after_field15}, "
            f"got 0x{extra_data[pos_after_field15]:02x if pos_after_field15 < len(extra_data) else 'EOF'}"
        )
        return extra_data

    # Field 17 has a fixed prefix of 0x01 0x01, then the end position as varint
    # Verify the fixed prefix
    field17_start = pos_after_field15 + 1  # Skip the 0x8F tag
    if field17_start + 2 >= len(extra_data):
        logger.debug("Field 17 too short for fixed prefix")
        return extra_data

    if extra_data[field17_start] != 0x01 or extra_data[field17_start + 1] != 0x01:
        logger.debug(
            f"Expected Field 17 prefix 01 01, got "
            f"{extra_data[field17_start]:02x} {extra_data[field17_start + 1]:02x}"
        )
        return extra_data

    # Decode the end position varint after the fixed prefix
    end_pos_start = field17_start + 2
    old_end_pos, pos_after_end = decode_varint(extra_data, end_pos_start)

    # Calculate new positions
    new_start_part2 = crdt_base_id + new_char_offset
    # End position is exclusive (start + length), not inclusive (start + length - 1)
    new_end_pos = crdt_base_id + new_char_offset + highlight_length

    # Encode new start CrdtId
    new_start_bytes = encode_crdt_id(author_id, new_start_part2)

    # Encode new end position varint
    new_end_bytes = encode_varint(new_end_pos)

    # Reconstruct:
    # Field15 tag + start CrdtId + Field17 tag + fixed prefix + end varint + rest
    new_extra = (
        bytes([CrdtFieldTags.FIELD_15_START_ANCHOR])
        + new_start_bytes
        + bytes([CrdtFieldTags.FIELD_17_END_POSITION, 0x01, 0x01])
        + new_end_bytes
        + extra_data[pos_after_end:]
    )

    old_start_offset = old_start_crdt[1] - crdt_base_id
    old_end_offset = old_end_pos - crdt_base_id

    logger.debug(
        f"Updated extra_value_data: start CrdtId ({author_id}, {old_start_crdt[1]})->({author_id}, {new_start_part2}) "
        f"[char {old_start_offset}->{new_char_offset}], "
        f"end pos {old_end_pos}->{new_end_pos} [char {old_end_offset}->{new_char_offset + highlight_length}]"
    )

    return new_extra


def get_crdt_base_id_from_rm(rm_file_path: Path) -> int:
    """Extract CRDT base ID from RootTextBlock in .rm file.

    The base ID is the item_id.part2 of the first CrdtSequenceItem in the
    RootTextBlock's text items.

    Args:
        rm_file_path: Path to .rm file

    Returns:
        Base ID (typically 16), or default 16 if not found
    """
    try:
        with open(rm_file_path, "rb") as f:
            for block in rmscene.read_blocks(f):
                if type(block).__name__ == "RootTextBlock":
                    for item in block.value.items.sequence_items():
                        return item.item_id.part2
    except Exception as e:
        logger.warning(f"Failed to get CRDT base ID from {rm_file_path}: {e}")

    return 16  # Default base ID
