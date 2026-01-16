import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--agent-url",
        default="http://localhost:9009",
        help="Agent URL (default: http://localhost:9009)",
    )


@pytest.fixture(scope="session")
def agent(request):
    """Agent URL fixture.

    If --agent-url is reachable, use it.
    Otherwise, start a local Green Agent server for the test session.
    """

    configured_url = request.config.getoption("--agent-url")

    def is_reachable(base_url: str, timeout_sec: float) -> bool:
        end = time.time() + timeout_sec
        while time.time() < end:
            try:
                response = httpx.get(f"{base_url}/.well-known/agent-card.json", timeout=1)
                if response.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    if is_reachable(configured_url, timeout_sec=2.0):
        yield configured_url
        return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    url = f"http://127.0.0.1:{port}"
    project_root = Path(__file__).resolve().parents[1]
    server_py = project_root / "src" / "server.py"

    proc = subprocess.Popen(
        [
            sys.executable,
            str(server_py),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--card-url",
            f"{url}/",
        ],
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        if not is_reachable(url, timeout_sec=10.0):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            pytest.exit(
                f"Could not connect to agent at {configured_url} and failed to start local agent at {url}",
                returncode=1,
            )

        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
