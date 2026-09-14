"""Thin, logged subprocess wrappers around the external EDA tools."""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence


@dataclass
class ToolResult:
    cmd: List[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    log_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, n: int = 25) -> str:
        lines = (self.stdout + "\n" + self.stderr).splitlines()
        return "\n".join(lines[-n:])


def run(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = 1800,
    log_path: Optional[Path] = None,
    check: bool = False,
) -> ToolResult:
    """Run a command, capture output, optionally tee it to ``log_path``."""
    t0 = time.time()
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        rc = 124
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")) + \
              f"\n*** TIMEOUT after {timeout}s ***"
    dt = time.time() - t0

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "$ " + " ".join(cmd) + f"\n\n[exit {rc} in {dt:.1f}s]\n\n"
            "===== stdout =====\n" + out + "\n===== stderr =====\n" + err
        )

    res = ToolResult(list(cmd), rc, out, err, dt, log_path)
    if check and not res.ok:
        raise RuntimeError(
            f"command failed ({rc}): {' '.join(cmd)}\n{res.tail()}"
        )
    return res


def yosys(script: str, *, workdir: Path, name: str, binary: str = "yosys",
          timeout: int = 1800) -> ToolResult:
    """Run a Yosys script given as a string."""
    workdir.mkdir(parents=True, exist_ok=True)
    ys = workdir / f"{name}.ys"
    ys.write_text(script)
    return run([binary, "-s", str(ys)], cwd=workdir,
               log_path=workdir / f"{name}.log", timeout=timeout)


def opensta(script: str, *, workdir: Path, name: str, binary: str = "sta",
            timeout: int = 1800) -> ToolResult:
    """Run an OpenSTA Tcl script given as a string."""
    workdir.mkdir(parents=True, exist_ok=True)
    tcl = workdir / f"{name}.tcl"
    tcl.write_text(script)
    return run([binary, "-no_splash", "-exit", str(tcl)], cwd=workdir,
               log_path=workdir / f"{name}.log", timeout=timeout)
