"""Helper functions for rmfakecloud test fixture."""

import json
import shutil
from pathlib import Path

from tests.fixtures.containerlib import (
    start_container,
    stop_container,
    wait_for_http_ready,
)


def get_credentials_path() -> Path:
    """Get path to rmfakecloud test credentials.

    Returns:
        Path to credentials.json file
    """
    return Path(__file__).parent / "credentials.json"


def get_credentials() -> dict:
    """Load rmfakecloud test credentials.

    Returns:
        Credentials dict with device_token, device_id, user_id

    Raises:
        FileNotFoundError: If credentials file not found
    """
    creds_path = get_credentials_path()
    if not creds_path.exists():
        raise FileNotFoundError(
            f"rmfakecloud credentials not found at {creds_path}"
        )
    return json.loads(creds_path.read_text())


def get_seed_path() -> Path:
    """Get path to rmfakecloud seed data directory.

    Returns:
        Path to seed directory
    """
    return Path(__file__).parent / "seed"


def start_rmfakecloud_container(
    runtime: str,
    container_name: str,
    port: int,
    seed_data: Path,
    test_data_dir: Path,
) -> tuple[str, bool]:
    """Start rmfakecloud container with fresh seed data.

    Args:
        runtime: Container runtime ("docker" or "podman")
        container_name: Name for the container
        port: Host port to map to container port 3000
        seed_data: Path to seed data directory
        test_data_dir: Path to temporary directory for this test's data

    Returns:
        Tuple of (url, success)
    """
    # Create fresh copy of seed data
    test_data = test_data_dir / "rmfakecloud"
    shutil.copytree(seed_data, test_data)

    url = f"http://localhost:{port}"

    # Clean up any existing container
    stop_container(runtime, container_name)

    # Start container
    image = "docker.io/ddvk/rmfakecloud:latest"
    env_vars = {
        "STORAGE_URL": f"http://localhost:{port}",
        "JWT_SECRET_KEY": "2vrOXKJWZ7zgEAf7CjN89rnPW/XOc0pH4naGClMRPxs=",
    }

    result = start_container(
        runtime=runtime,
        image=image,
        container_name=container_name,
        port_mapping=f"{port}:3000",
        volume_mapping=f"{test_data}:/data:Z",
        env_vars=env_vars,
    )

    if result.returncode != 0:
        return url, False

    # Wait for ready
    if not wait_for_http_ready(url, timeout=30.0):
        stop_container(runtime, container_name)
        return url, False

    return url, True


def allocate_port(request) -> tuple[int, str]:
    """Allocate a port for rmfakecloud, supporting pytest-xdist.

    Args:
        request: pytest request object

    Returns:
        Tuple of (port, container_name)
    """
    worker_id = getattr(request.config, "workerinput", None)
    if worker_id:
        worker_index = int(worker_id["workerid"].replace("gw", ""))
        port = 3001 + worker_index
        container_name = f"test_rmfakecloud_worker{worker_index}"
    else:
        port = 3001
        container_name = "test_rmfakecloud"

    return port, container_name
