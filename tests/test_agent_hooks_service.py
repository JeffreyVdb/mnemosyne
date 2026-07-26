from __future__ import annotations

import os
import plistlib
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from integrations.agent_hooks.client import SidecarClient
from integrations.agent_hooks.transport import SOCKET_ENV

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "integrations" / "agent_hooks" / "run_sidecar.py"
SYSTEMD_TEMPLATE = (
    ROOT
    / "integrations"
    / "agent_hooks"
    / "services"
    / "mnemosyne-agent-hooks-sidecar.service.in"
)
LAUNCHD_TEMPLATE = (
    ROOT
    / "integrations"
    / "agent_hooks"
    / "services"
    / "com.mnemosyne.agent-hooks-sidecar.plist.in"
)
PYTHON_TOKEN = "@MNEMOSYNE_PYTHON@"
LAUNCHER_TOKEN = "@MNEMOSYNE_SIDECAR_LAUNCHER@"


def _substituted_template(path: Path) -> str:
    return (
        path.read_text()
        .replace(PYTHON_TOKEN, sys.executable)
        .replace(LAUNCHER_TOKEN, str(LAUNCHER))
    )


def _wait_for_health(
    process: subprocess.Popen[str], socket_path: Path
) -> SidecarClient:
    client = SidecarClient(socket_path=socket_path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"Sidecar exited with {process.returncode}: "
                f"stdout={stdout!r}, stderr={stderr!r}"
            )
        if client.health().ok:
            return client
        time.sleep(0.02)
    raise AssertionError("Sidecar did not answer health")


def _launch_sidecar(
    *,
    cwd: Path,
    socket_path: Path,
    home: Path,
    launcher: Path = LAUNCHER,
    print_import_provenance: bool = False,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env[SOCKET_ENV] = str(socket_path)
    env["HOME"] = str(home)
    env.pop("PYTHONPATH", None)
    argv = [sys.executable, "-I"]
    argv.append(str(launcher))
    if print_import_provenance:
        argv.append("--print-import-provenance")
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)


def test_systemd_template_has_managed_service_guarantees() -> None:
    template = SYSTEMD_TEMPLATE.read_text()
    rendered = _substituted_template(SYSTEMD_TEMPLATE)

    assert f"ExecStart={PYTHON_TOKEN} -I {LAUNCHER_TOKEN}" in template
    assert "Restart=on-failure" in template
    assert "WantedBy=default.target" in template
    assert "EnvironmentFile=-%h/.config/mnemosyne/llm.env" in template
    assert "/home/" not in template

    exec_start = re.search(r"^ExecStart=(.+)$", rendered, re.MULTILINE)
    assert exec_start is not None
    interpreter, flag, launcher = exec_start.group(1).split()
    assert Path(interpreter).is_absolute()
    assert flag == "-I"
    assert Path(launcher).is_absolute()


def test_launchd_template_has_managed_service_guarantees() -> None:
    template = LAUNCHD_TEMPLATE.read_text()
    rendered = _substituted_template(LAUNCHD_TEMPLATE)
    definition = plistlib.loads(rendered.encode())

    assert template.count(PYTHON_TOKEN) == 1
    assert template.count(LAUNCHER_TOKEN) == 1
    assert "/home/" not in template
    assert definition["ProgramArguments"] == [
        sys.executable,
        "-I",
        str(LAUNCHER),
    ]
    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] == {"SuccessfulExit": False}


def test_isolated_launcher_ignores_working_directory_packages(
    tmp_path: Path,
) -> None:
    hostile_cwd = tmp_path / "hostile"
    hostile_package = hostile_cwd / "integrations" / "agent_hooks"
    hostile_package.mkdir(parents=True)
    (hostile_cwd / "integrations" / "__init__.py").write_text("")
    (hostile_package / "__init__.py").write_text("")
    hostile_sidecar = hostile_package / "sidecar.py"
    hostile_sidecar.write_text("raise RuntimeError('HOSTILE SIDECAR RAN')\n")

    provenances: list[str] = []
    for index, cwd in enumerate((ROOT, hostile_cwd)):
        home = tmp_path / f"home-{index}"
        home.mkdir()
        socket_path = tmp_path / f"sidecar-{index}.sock"
        process = _launch_sidecar(
            cwd=cwd,
            socket_path=socket_path,
            home=home,
            print_import_provenance=True,
        )
        try:
            provenance = process.stdout.readline().strip()
            client = _wait_for_health(process, socket_path)
            assert client.health().ok is True
        finally:
            _stdout, stderr = _stop(process)

        assert "HOSTILE SIDECAR RAN" not in stderr
        assert str(hostile_sidecar) not in stderr
        print(f"{cwd}: {provenance}")
        provenances.append(str(Path(provenance).resolve()))

    assert (
        provenances
        == [str((ROOT / "integrations/agent_hooks/sidecar.py").resolve())] * 2
    )


def test_isolated_launcher_resolves_symlink_before_importing(
    tmp_path: Path,
) -> None:
    launcher_dir = tmp_path / "a" / "b"
    launcher_dir.mkdir(parents=True)
    linked_launcher = launcher_dir / "run_sidecar.py"
    linked_launcher.symlink_to(LAUNCHER)

    hostile_package = tmp_path / "integrations" / "agent_hooks"
    hostile_package.mkdir(parents=True)
    (tmp_path / "integrations" / "__init__.py").write_text("")
    (hostile_package / "__init__.py").write_text("")
    hostile_sidecar = hostile_package / "sidecar.py"
    hostile_sidecar.write_text("raise RuntimeError('HOSTILE SIDECAR RAN')\n")

    home = tmp_path / "home"
    home.mkdir()
    process = _launch_sidecar(
        cwd=tmp_path,
        socket_path=tmp_path / "sidecar.sock",
        home=home,
        launcher=linked_launcher,
        print_import_provenance=True,
    )
    try:
        provenance = process.stdout.readline().strip()
        client = _wait_for_health(process, tmp_path / "sidecar.sock")
        assert client.health().ok is True
    finally:
        _stdout, stderr = _stop(process)

    assert "HOSTILE SIDECAR RAN" not in stderr
    assert str(hostile_sidecar) not in stderr
    assert (
        Path(provenance).resolve()
        == (ROOT / "integrations" / "agent_hooks" / "sidecar.py").resolve()
    )


@pytest.mark.parametrize(
    ("exception_expression", "message"),
    [
        (
            'RuntimeError("provider registration refused")',
            "provider registration refused",
        ),
        (
            'sqlite3.OperationalError("unable to open database file")',
            "unable to open database file",
        ),
    ],
)
def test_provider_cache_startup_failure_is_one_line(
    tmp_path: Path,
    exception_expression: str,
    message: str,
) -> None:
    fake_dependency = tmp_path / "fake-dependency"
    fake_dependency.mkdir()
    (fake_dependency / "hermes_memory_provider.py").write_text(
        f"""
import sqlite3

class MnemosyneMemoryProvider:
    pass

class PrefetchProfile:
    def __init__(self, **_kwargs):
        pass

def register_profile(_profile):
    raise {exception_expression}
""".lstrip()
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(fake_dependency), str(ROOT)))
    env[SOCKET_ENV] = str(tmp_path / "sidecar.sock")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "from integrations.agent_hooks.sidecar import main; main()",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert process.returncode == 1
    assert process.stdout == ""
    assert process.stderr == f"Sidecar failed to start: {message}\n"


def test_departed_peers_do_not_emit_tracebacks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    socket_path = tmp_path / "sidecar.sock"
    process = _launch_sidecar(
        cwd=ROOT,
        socket_path=socket_path,
        home=home,
    )
    aborted_requests = 10
    try:
        _wait_for_health(process, socket_path)
        body = b'{"prompt":"remember this","session_id":"departed-peer"}'
        request = (
            b"POST /prefetch HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        for _ in range(aborted_requests):
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peer.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("ii", 1, 0),
            )
            peer.connect(str(socket_path))
            peer.sendall(request)
            peer.close()
        time.sleep(1)
        assert SidecarClient(socket_path=socket_path).health().ok is True
    finally:
        _stdout, stderr = _stop(process)

    assert "Traceback" not in stderr
    assert len(stderr.splitlines()) <= aborted_requests
