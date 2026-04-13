import logging
import os
from urllib.parse import urlparse, urlunparse

import docker
from docker.errors import APIError, DockerException

from openhands.utils.environment import is_running_in_docker

logger = logging.getLogger(__name__)


def _get_docker_client() -> docker.DockerClient | None:
    """Get a Docker client, returning None if unavailable."""
    try:
        return docker.from_env()
    except DockerException:
        return None


def get_bridge_gateway_ip() -> str | None:
    """Get the Docker bridge network gateway IP address.

    On native Linux Docker (without Docker Desktop), the host-gateway DNS alias
    doesn't resolve automatically. This function inspects the default bridge
    network to find the actual gateway IP that can be used for extra_hosts.

    This function uses multiple methods:
    1. Docker Python SDK (if available and has permissions)
    2. Docker CLI command (fallback)
    3. Default common addresses (ultimate fallback)

    Returns:
        The bridge network gateway IP, or None if unavailable

    Example:
        >>> ip = get_bridge_gateway_ip()
        >>> if ip:
        >>>     extra_hosts = {'host.docker.internal': ip}
    """
    # Method 1: Try Docker SDK
    client = None
    try:
        try:
            client = docker.from_env()
        except (DockerException, Exception):
            # Permission error or Docker not available - try CLI instead
            pass

        if client:
            try:
                network = client.networks.get('bridge')
                ipam = network.attrs.get('IPAM', {})
                for config in ipam.get('Config', []):
                    gateway = config.get('Gateway')
                    if gateway and gateway != '0.0.0.0':
                        return gateway
            except (APIError, Exception):
                pass

            try:
                client.close()
            except Exception:
                pass
    except Exception:
        pass

    # Method 2: Try Docker CLI as fallback
    try:
        import subprocess
        result = subprocess.run(
            ['docker', 'network', 'inspect', 'bridge'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            if data and len(data) > 0:
                ipam = data[0].get('IPAM', {})
                for config in ipam.get('Config', []):
                    gateway = config.get('Gateway')
                    if gateway and gateway != '0.0.0.0':
                        return gateway
    except Exception:
        pass

    # Method 3: Return common default (works in most environments)
    # This is the most common Docker bridge gateway on Linux
    return None


def detect_docker_host_ip() -> str:
    """Detect the appropriate host IP for Docker container communication.

    This function attempts to detect the correct IP address that Docker
    containers can use to reach the host machine.

    Priority:
    1. OH_SANDBOX_HOST_IP environment variable (explicit override)
    2. Bridge network gateway IP (detected automatically)
    3. Default gateway from routing table

    Returns:
        The detected host IP address
    """
    # 1. Check for explicit environment variable
    env_ip = os.environ.get('OH_SANDBOX_HOST_IP')
    if env_ip:
        return env_ip

    # 2. Try to detect bridge gateway
    bridge_ip = get_bridge_gateway_ip()
    if bridge_ip:
        return bridge_ip

    # 3. Fall back to default route
    try:
        # Read the default gateway from /proc/net/route
        with open('/proc/net/route', 'r') as f:
            for line in f:
                parts = line.strip().split()
                if parts[0] == '00000000' and parts[1] != '00000000':
                    # Convert hex IP to dotted decimal
                    hex_ip = parts[1]
                    ip = '.'.join(str(int(hex_ip[i:i+2], 16)) for i in range(0, 8, 2))
                    return ip
    except Exception:
        pass

    # 4. Ultimate fallback
    logger.warning('Could not detect host IP, using 172.17.0.1')
    return '172.17.0.1'


def replace_localhost_hostname_for_docker(
    url: str, replacement: str = 'host.docker.internal'
) -> str:
    """Replace localhost hostname in URL with the specified replacement when running in Docker.

    This function only performs the replacement when the code is running inside a Docker
    container. When not running in Docker, it returns the original URL unchanged.

    Only replaces the hostname if it's exactly 'localhost', preserving all other
    parts of the URL including port, path, query parameters, etc.

    Args:
        url: The URL to process
        replacement: The hostname to replace localhost with (default: 'host.docker.internal')

    Returns:
        URL with localhost hostname replaced if running in Docker and hostname is localhost,
        otherwise returns the original URL unchanged
    """
    if not is_running_in_docker():
        return url
    parsed = urlparse(url)
    if parsed.hostname == 'localhost':
        # Replace only the hostname part, preserving port and everything else
        netloc = parsed.netloc.replace('localhost', replacement, 1)
        return urlunparse(parsed._replace(netloc=netloc))
    return url
