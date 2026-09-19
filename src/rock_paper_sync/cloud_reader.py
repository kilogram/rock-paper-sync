"""Read-only cloud protocol for pull operations.

Defines the ``CloudReader`` protocol — the minimal read interface that
pull-side code may depend on.  ``RmCloudSync`` satisfies it structurally
(no explicit inheritance required).

Invariants relied upon:
    INV-I3: Pull cannot write to the cloud.  ``pull_sync`` and ``ink/*``
            accept ``CloudReader`` only; import-linter forbids
            ``sync_v3``, ``rm_cloud_sync`` there.

Import restrictions (enforced by import-linter):
    This module must NOT import ``weasyprint``, ``fitz``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from rock_paper_sync.render.wordmap import DocumentIndexEntry

__all__ = ["CloudReader"]


@runtime_checkable
class CloudReader(Protocol):
    """Read-only interface to the reMarkable cloud.

    Methods mirror the subset of ``RmCloudSync`` used by pull operations.
    """

    def get_document_index_hash(self, doc_uuid: str) -> str | None:
        """Get the document index hash for a specific document.

        Args:
            doc_uuid: UUID of the document.

        Returns:
            Document index hash, or ``None`` if not found.
        """
        ...

    def download_page_rm_files(
        self,
        doc_uuid: str,
        page_uuids: list[str],
        output_dir: Path,
    ) -> list[Path | None]:
        """Download .rm files for document pages.

        Args:
            doc_uuid: Document UUID.
            page_uuids: Page UUIDs to download.
            output_dir: Directory to save files to.

        Returns:
            List of paths (or ``None`` per page if download failed).
        """
        ...

    def get_existing_page_uuids(self, doc_uuid: str) -> list[str]:
        """Get page UUIDs for a document.

        Args:
            doc_uuid: Document UUID.

        Returns:
            Ordered list of page UUIDs, empty if document not found.
        """
        ...

    def download_content_file(self, doc_uuid: str) -> bytes | None:
        """Download the ``.content`` file for a document.

        Args:
            doc_uuid: Document UUID.

        Returns:
            Raw JSON bytes of the ``.content`` file, or ``None`` if not found.
        """
        ...

    def download_document_blob(self, doc_uuid: str, entry_name: str) -> bytes | None:
        """Download a specific blob from a document's index.

        Args:
            doc_uuid: Document UUID.
            entry_name: Full entry name (e.g. ``"{uuid}.pdf"``).

        Returns:
            Raw blob bytes, or ``None`` if not found.
        """
        ...

    def get_document_index_entries(self, doc_uuid: str) -> list[DocumentIndexEntry] | None:
        """Get the document index entries (names and hashes) without downloading blobs.

        Used by pull to compare the ``.pdf`` entry hash with
        ``pushed_pdf_sha256`` (I10).

        Args:
            doc_uuid: Document UUID.

        Returns:
            List of ``DocumentIndexEntry``, or ``None`` if document not found.
        """
        ...
