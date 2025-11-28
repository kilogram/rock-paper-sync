"""Container runtime utilities for test fixtures.

Provides container runtime detection and helper functions for managing
containerized services in tests.
"""

import shutil
import subprocess
import time

import requests


def get_container_runtime() -> str | None:
    """Detect available container runtime.

    Returns:
        "podman", "docker", or None if neither found
    """
    if shutil.which("podman"):
        return "podman"
    if shutil.which("docker"):
        return "docker"
    return None


def wait_for_http_ready(
    url: str, timeout: float = 30.0, interval: float = 0.5, endpoint: str = "/health"
) -> bool:
    """Wait for HTTP service to become ready.

    Args:
        url: Base URL of service
        timeout: Maximum time to wait in seconds
        interval: Time between checks in seconds
        endpoint: Health check endpoint path

    Returns:
        True if service became ready, False if timeout
    """
    start = time.time()
    full_url = f"{url}{endpoint}"

    while time.time() - start < timeout:
        try:
            resp = requests.get(full_url, timeout=2)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(interval)

    return False


def stop_container(runtime: str, container_name: str) -> None:
    """Stop and remove a container.

    Args:
        runtime: Container runtime ("docker" or "podman")
        container_name: Name of container to stop
    """
    subprocess.run([runtime, "stop", container_name], capture_output=True)
    subprocess.run([runtime, "rm", "-f", container_name], capture_output=True)


def start_container(
    runtime: str,
    image: str,
    container_name: str,
    port_mapping: str,
    volume_mapping: str | None = None,
    env_vars: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Start a container with specified configuration.

    Args:
        runtime: Container runtime ("docker" or "podman")
        image: Container image to run
        container_name: Name for the container
        port_mapping: Port mapping in format "host:container"
        volume_mapping: Volume mapping in format "host:container:flags"
        env_vars: Environment variables as dict
        extra_args: Additional arguments to pass to run command

    Returns:
        CompletedProcess result from container start
    """
    cmd = [
        runtime,
        "run",
        "-d",
        "--name",
        container_name,
        "-p",
        port_mapping,
    ]

    # Add environment variables
    if env_vars:
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])

    # Add volume mapping
    if volume_mapping:
        cmd.extend(["-v", volume_mapping])

    # Add extra args
    if extra_args:
        cmd.extend(extra_args)

    # Add image
    cmd.append(image)

    return subprocess.run(cmd, capture_output=True, text=True)
