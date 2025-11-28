"""Comprehensive audit logging for forensic analysis.

This module provides structured audit logging of all file operations,
cloud API calls, and configuration changes. Logs are written in JSON
format for easy parsing and analysis.

Audit logs include:
- Complete operation details (type, vault, file path, timestamps)
- File metadata (hash, size, UUIDs, page count)
- Cloud API calls (method, endpoint, request/response data)
- Configuration snapshots (vault settings, sync settings)
- Success/failure status with detailed error information
- User actions (command invoked, flags used)
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("rock_paper_sync.audit")


@dataclass
class AuditEvent:
    """Structured audit event for forensic logging.

    Attributes:
        event_type: Type of event (sync, unsync, delete, cloud_upload, etc.)
        timestamp: Unix timestamp in milliseconds
        timestamp_iso: ISO 8601 formatted timestamp
        vault_name: Name of vault involved (if applicable)
        file_path: Relative path within vault (if applicable)
        operation: Specific operation performed
        status: success, failure, or partial
        details: Additional operation-specific data
        error: Error message if status is failure
        user_action: Command/action that triggered this event
    """

    event_type: str
    timestamp: int
    timestamp_iso: str
    operation: str
    status: str
    vault_name: str | None = None
    file_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    user_action: str | None = None

    def to_json(self) -> str:
        """Convert to JSON string for logging."""
        return json.dumps(asdict(self), default=str)


class AuditLogger:
    """Structured audit logger for comprehensive forensic logging.

    This logger creates detailed audit trails of all operations,
    providing enough information for forensic analysis and debugging.
    """

    def __init__(self, audit_file: Path | None = None):
        """Initialize audit logger.

        Args:
            audit_file: Optional path to dedicated audit log file.
                       If None, uses standard logging only.
        """
        self.audit_file = audit_file
        if audit_file:
            audit_file.parent.mkdir(parents=True, exist_ok=True)

    def _create_event(
        self,
        event_type: str,
        operation: str,
        status: str,
        vault_name: str | None = None,
        file_path: str | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
        user_action: str | None = None,
    ) -> AuditEvent:
        """Create audit event with current timestamp."""
        now = time.time()
        timestamp_ms = int(now * 1000)
        timestamp_iso = datetime.fromtimestamp(now).isoformat()

        return AuditEvent(
            event_type=event_type,
            timestamp=timestamp_ms,
            timestamp_iso=timestamp_iso,
            operation=operation,
            status=status,
            vault_name=vault_name,
            file_path=file_path,
            details=details or {},
            error=error,
            user_action=user_action,
        )

    def _log_event(self, event: AuditEvent) -> None:
        """Log audit event to file and standard logger."""
        json_str = event.to_json()

        # Log to standard logger at INFO level
        logger.info(f"AUDIT: {json_str}")

        # Also write to dedicated audit file if configured
        if self.audit_file:
            try:
                with open(self.audit_file, "a", encoding="utf-8") as f:
                    f.write(json_str + "\n")
            except Exception as e:
                logger.error(f"Failed to write to audit file: {e}")

    def log_sync_start(
        self,
        vault_name: str,
        file_path: str,
        file_hash: str,
        file_size: int,
        user_action: str | None = None,
    ) -> None:
        """Log start of file sync operation.

        Args:
            vault_name: Vault name
            file_path: Relative file path
            file_hash: SHA-256 hash of file content
            file_size: File size in bytes
            user_action: Command that triggered sync
        """
        event = self._create_event(
            event_type="sync",
            operation="sync_start",
            status="in_progress",
            vault_name=vault_name,
            file_path=file_path,
            details={
                "file_hash": file_hash,
                "file_size_bytes": file_size,
            },
            user_action=user_action,
        )
        self._log_event(event)

    def log_sync_success(
        self,
        vault_name: str,
        file_path: str,
        remarkable_uuid: str,
        page_count: int,
        file_hash: str,
        previous_uuid: str | None = None,
        user_action: str | None = None,
    ) -> None:
        """Log successful file sync.

        Args:
            vault_name: Vault name
            file_path: Relative file path
            remarkable_uuid: Document UUID on reMarkable
            page_count: Number of pages generated
            file_hash: SHA-256 hash of file content
            previous_uuid: Previous UUID if this was an update
            user_action: Command that triggered sync
        """
        details = {
            "remarkable_uuid": remarkable_uuid,
            "page_count": page_count,
            "file_hash": file_hash,
        }
        if previous_uuid:
            details["previous_uuid"] = previous_uuid
            details["operation_type"] = "update"
        else:
            details["operation_type"] = "create"

        event = self._create_event(
            event_type="sync",
            operation="sync_complete",
            status="success",
            vault_name=vault_name,
            file_path=file_path,
            details=details,
            user_action=user_action,
        )
        self._log_event(event)

    def log_sync_failure(
        self,
        vault_name: str,
        file_path: str,
        error: str,
        file_hash: str | None = None,
        user_action: str | None = None,
    ) -> None:
        """Log failed file sync.

        Args:
            vault_name: Vault name
            file_path: Relative file path
            error: Error message
            file_hash: SHA-256 hash if available
            user_action: Command that triggered sync
        """
        details = {}
        if file_hash:
            details["file_hash"] = file_hash

        event = self._create_event(
            event_type="sync",
            operation="sync_failed",
            status="failure",
            vault_name=vault_name,
            file_path=file_path,
            details=details,
            error=error,
            user_action=user_action,
        )
        self._log_event(event)

    def log_cloud_upload(
        self,
        doc_uuid: str,
        file_count: int,
        total_size: int,
        broadcast: bool,
        vault_name: str | None = None,
        file_path: str | None = None,
    ) -> None:
        """Log cloud API upload operation.

        Args:
            doc_uuid: Document UUID
            file_count: Number of files uploaded
            total_size: Total size in bytes
            broadcast: Whether sync notification was sent
            vault_name: Vault name if applicable
            file_path: File path if applicable
        """
        event = self._create_event(
            event_type="cloud_api",
            operation="upload_document",
            status="success",
            vault_name=vault_name,
            file_path=file_path,
            details={
                "doc_uuid": doc_uuid,
                "file_count": file_count,
                "total_size_bytes": total_size,
                "broadcast": broadcast,
            },
        )
        self._log_event(event)

    def log_cloud_delete(
        self,
        doc_uuid: str,
        vault_name: str | None = None,
        file_path: str | None = None,
        broadcast: bool = True,
    ) -> None:
        """Log cloud API delete operation.

        Args:
            doc_uuid: Document UUID
            vault_name: Vault name if applicable
            file_path: File path if applicable
            broadcast: Whether sync notification was sent
        """
        event = self._create_event(
            event_type="cloud_api",
            operation="delete_document",
            status="success",
            vault_name=vault_name,
            file_path=file_path,
            details={
                "doc_uuid": doc_uuid,
                "broadcast": broadcast,
            },
        )
        self._log_event(event)

    def log_unsync(
        self,
        vault_name: str,
        files_removed: int,
        files_deleted_from_cloud: int,
        delete_from_cloud: bool,
        user_action: str | None = None,
    ) -> None:
        """Log vault unsync operation.

        Args:
            vault_name: Vault name
            files_removed: Number of files removed from sync state
            files_deleted_from_cloud: Number actually deleted from cloud
            delete_from_cloud: Whether cloud deletion was requested
            user_action: Command that triggered unsync
        """
        event = self._create_event(
            event_type="unsync",
            operation="unsync_vault",
            status="success",
            vault_name=vault_name,
            details={
                "files_removed_from_state": files_removed,
                "files_deleted_from_cloud": files_deleted_from_cloud,
                "delete_from_cloud_requested": delete_from_cloud,
            },
            user_action=user_action,
        )
        self._log_event(event)

    def log_config_load(
        self,
        config_path: Path,
        vault_count: int,
        vault_names: list[str],
    ) -> None:
        """Log configuration load event.

        Args:
            config_path: Path to config file
            vault_count: Number of vaults configured
            vault_names: List of vault names
        """
        event = self._create_event(
            event_type="config",
            operation="load_config",
            status="success",
            details={
                "config_path": str(config_path),
                "vault_count": vault_count,
                "vault_names": vault_names,
            },
        )
        self._log_event(event)

    def log_state_reset(
        self,
        user_action: str | None = None,
    ) -> None:
        """Log state database reset.

        Args:
            user_action: Command that triggered reset
        """
        event = self._create_event(
            event_type="state",
            operation="reset_state",
            status="success",
            details={},
            user_action=user_action,
        )
        self._log_event(event)


# Global audit logger instance
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Get global audit logger instance.

    Returns:
        Global AuditLogger instance
    """
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def initialize_audit_logger(audit_file: Path | None = None) -> None:
    """Initialize global audit logger with optional dedicated file.

    Args:
        audit_file: Optional path to dedicated audit log file
    """
    global _audit_logger
    _audit_logger = AuditLogger(audit_file=audit_file)
    logger.debug(f"Audit logger initialized: audit_file={audit_file}")
