"""rm_cloud API client - pretends to be a reMarkable device."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class DeviceCredentials:
    """Device authentication credentials for rm_cloud."""

    device_token: str
    device_id: str
    user_id: str


class RmCloudClient:
    """Client that authenticates as a device and triggers sync notifications."""

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        credentials_path: Optional[Path] = None,
    ):
        """
        Initialize rm_cloud client.

        Args:
            base_url: Base URL of rm_cloud instance
            credentials_path: Path to store/load device credentials
        """
        self.base_url = base_url.rstrip("/")
        self.credentials_path = credentials_path or Path.home() / ".config" / "rock-paper-sync" / "device-credentials.json"
        self.credentials: Optional[DeviceCredentials] = None
        self._load_credentials()

    def _load_credentials(self) -> None:
        """Load device credentials from disk if they exist."""
        if self.credentials_path.exists():
            try:
                data = json.loads(self.credentials_path.read_text())
                self.credentials = DeviceCredentials(
                    device_token=data["device_token"],
                    device_id=data["device_id"],
                    user_id=data["user_id"],
                )
                logger.info(f"Loaded device credentials for device: {self.credentials.device_id}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load credentials: {e}")

    def _save_credentials(self) -> None:
        """Save device credentials to disk."""
        if not self.credentials:
            return

        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "device_token": self.credentials.device_token,
            "device_id": self.credentials.device_id,
            "user_id": self.credentials.user_id,
        }
        self.credentials_path.write_text(json.dumps(data, indent=2))
        logger.info(f"Saved device credentials to {self.credentials_path}")

    def register_device(self, one_time_code: str, device_id: str = "rock-paper-sync-001") -> DeviceCredentials:
        """
        Register this client as a new device with rm_cloud.

        Args:
            one_time_code: Code obtained from rm_cloud web UI
            device_id: Unique identifier for this device

        Returns:
            Device credentials including JWT token

        Raises:
            requests.HTTPError: If registration fails
        """
        url = f"{self.base_url}/token/json/2/device/new"
        payload = {
            "code": one_time_code.lower(),
            "deviceDesc": "rock-paper-sync (Obsidian to reMarkable sync)",
            "deviceID": device_id,
        }

        logger.info(f"Registering device: {device_id}")
        response = requests.post(url, json=payload)
        response.raise_for_status()

        # Response format: just the JWT token as a string
        device_token = response.text.strip('"')

        # Extract user_id from the token (it's in the JWT payload)
        # For now, we'll need to decode the JWT to get the user_id
        # But we can also get it from other API calls, so we'll set it to empty for now
        # and update it when we make authenticated calls
        self.credentials = DeviceCredentials(
            device_token=device_token,
            device_id=device_id,
            user_id="",  # Will be populated from JWT claims
        )

        self._save_credentials()
        logger.info("Device registered successfully")
        return self.credentials

    def get_user_token(self) -> str:
        """
        Renew/get user access token from device token.

        Returns:
            User access token (JWT)

        Raises:
            ValueError: If device is not registered
            requests.HTTPError: If token renewal fails
        """
        if not self.credentials:
            raise ValueError("Device not registered. Call register_device() first.")

        url = f"{self.base_url}/token/json/2/user/new"
        headers = {"Authorization": f"Bearer {self.credentials.device_token}"}

        logger.debug("Renewing user token")
        response = requests.post(url, headers=headers)
        response.raise_for_status()

        user_token = response.text.strip('"')
        return user_token

    def trigger_sync(self) -> str:
        """
        Trigger sync notification to all connected devices.

        This tells xochitl and other devices to reload/resync.

        Returns:
            Notification ID

        Raises:
            ValueError: If device is not registered
            requests.HTTPError: If sync trigger fails
        """
        if not self.credentials:
            raise ValueError("Device not registered. Call register_device() first.")

        # Use the device token for authentication
        url = f"{self.base_url}/api/v1/sync-complete"
        headers = {"Authorization": f"Bearer {self.credentials.device_token}"}

        logger.info("Triggering sync notification")
        response = requests.post(url, headers=headers)
        response.raise_for_status()

        result = response.json()
        notification_id = result.get("id", "")
        logger.info(f"Sync notification sent: {notification_id}")
        return notification_id

    def is_registered(self) -> bool:
        """Check if device is registered."""
        return self.credentials is not None
