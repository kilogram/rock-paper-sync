"""Metadata generation for reMarkable documents.

This module generates the JSON metadata files required by reMarkable v6 format.
Each document needs:
- {uuid}.metadata - Document-level metadata (name, parent, timestamps)
- {uuid}.content - Content structure (pages, tool settings)
- {page-uuid}-metadata.json - Per-page layer information
"""

import time
from typing import Any


def current_rm_timestamp() -> int:
    """Get current time as 13-digit Unix timestamp (milliseconds).

    reMarkable uses millisecond timestamps for all time fields.

    Returns:
        Current timestamp in milliseconds since epoch

    Example:
        >>> ts = current_rm_timestamp()
        >>> len(str(ts))
        13
    """
    return int(time.time() * 1000)


def generate_document_metadata(
    visible_name: str,
    parent_uuid: str,
    modified_time: int,
) -> dict[str, Any]:
    """Generate .metadata file content for a document.

    Args:
        visible_name: Display name shown in reMarkable UI
        parent_uuid: UUID of parent folder (empty string for root)
        modified_time: Last modified timestamp (milliseconds since epoch)

    Returns:
        Dictionary ready for JSON serialization

    Example:
        >>> metadata = generate_document_metadata("My Note", "", 1700000000000)
        >>> metadata['visibleName']
        'My Note'
    """
    return {
        "deleted": False,
        "lastModified": str(modified_time),
        "lastOpened": "",
        "lastOpenedPage": 0,
        "metadatamodified": False,
        "modified": False,
        "parent": parent_uuid,
        "pinned": False,
        "synced": True,
        "type": "DocumentType",
        "version": 1,
        "visibleName": visible_name,
    }


def generate_content_metadata(page_uuids: list[str]) -> dict[str, Any]:
    """Generate .content file content.

    Specifies pages, layout settings, and default tool preferences.

    Args:
        page_uuids: List of page UUIDs in order

    Returns:
        Dictionary ready for JSON serialization

    Example:
        >>> content = generate_content_metadata(["abc-123", "def-456"])
        >>> content['pageCount']
        2
    """
    return {
        "coverPageNumber": 0,
        "documentMetadata": {},
        "extraMetadata": {
            "LastBrushColor": "Black",
            "LastBrushThicknessScale": "2",
            "LastColor": "Black",
            "LastEraserThicknessScale": "2",
            "LastEraserTool": "Eraser",
            "LastPen": "Ballpointv2",
            "LastPenColor": "Black",
            "LastPenThicknessScale": "2",
            "LastReplacementColor": "Black",
            "LastTool": "Ballpointv2",
        },
        "fileType": "notebook",
        "fontName": "",
        "lineHeight": -1,
        "margins": 100,
        "orientation": "portrait",
        "pageCount": len(page_uuids),
        "pages": page_uuids,
        "textAlignment": "left",
        "textScale": 1,
    }


def generate_page_metadata() -> dict[str, Any]:
    """Generate page metadata JSON.

    Each page needs a metadata file with layer information.

    Returns:
        Dictionary ready for JSON serialization

    Example:
        >>> page_meta = generate_page_metadata()
        >>> page_meta['layers'][0]['name']
        'Layer 1'
    """
    return {
        "layers": [
            {
                "name": "Layer 1",
                "visible": True,
            }
        ]
    }


def generate_folder_metadata(name: str, parent_uuid: str) -> dict[str, Any]:
    """Generate folder (CollectionType) metadata.

    Args:
        name: Folder name shown in reMarkable UI
        parent_uuid: UUID of parent folder (empty string for root)

    Returns:
        Dictionary ready for JSON serialization

    Example:
        >>> folder = generate_folder_metadata("Projects", "")
        >>> folder['type']
        'CollectionType'
    """
    timestamp = int(time.time() * 1000)
    return {
        "deleted": False,
        "lastModified": str(timestamp),
        "lastOpened": "",
        "metadatamodified": False,
        "modified": False,
        "parent": parent_uuid,
        "pinned": False,
        "synced": True,
        "type": "CollectionType",
        "version": 1,
        "visibleName": name,
    }
