"""One-click launcher for the 0719agent Commerce platform.

Starts up to three services in the background:
  1. mock_platform  (FastAPI on port 8001) — emulates Shopify
  2. api            (FastAPI on port 8000) — the agent + business layer
  3. ecommerce      (FastAPI on port 8002) — full Vue 3 e-commerce platform

Each service is started as a subprocess; logs are prefixed with [mock],
[api], or [shop]. Ctrl+C kills all.

Usage:
    python -m scripts.run_all
    python scripts/run_all.py

Set ECOMMERCE_SKIP=1 in the env to skip the e-commerce service (e.g. when
PostgreSQL is not installed yet).
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("run_all")


def start(cmd: list[str], tag: str) -> subprocess.Popen:
    """Start a subprocess that inherits a tagged stdout/stderr."""
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    log.info("[%s] started pid=%s cmd=%s", tag, proc.pid, " ".join(cmd))
    return proc


def wait_for(url: str, tag: str, timeout: float = 30.0) -> bool:
    """Poll a /health endpoint until it returns 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                log.info("[%s] ready at %s", tag, url)
                return True
        except Exception:
            time.sleep(0.5)
    log.error("[%s] NOT ready at %s after %.0fs", tag, url, timeout)
    return False


def stream_output(proc: subprocess.Popen, tag: str) -> None:
    """Print subprocess stdout with a tag prefix. Runs until EOF."""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{tag}] {line}")
        sys.stdout.flush()


def main() -> int:
    skip_ecommerce = os.environ.get("ECOMMERCE_SKIP", "") == "1"

    mock_cmd = [PYTHON, "-m", "mock_platform.server"]
    api_cmd = [PYTHON, "-m", "api.server"]
    shop_cmd = [PYTHON, "-m", "ecommerce.server"]

    mock_proc = start(mock_cmd, "mock")
    api_proc = start(api_cmd, "api")
    shop_proc = None if skip_ecommerce else start(shop_cmd, "shop")

    procs = [(mock_proc, "mock"), (api_proc, "api")]
    if shop_proc is not None:
        procs.append((shop_proc, "shop"))

    # Signal handlers + atexit must be wired up BEFORE we start streaming
    # stdout, so Ctrl+C during the (slow) health-check phase still
    # terminates every child.
    _is_signalled = [False]

    def cleanup(*_):
        for p, t in procs:
            try:
                p.terminate()
                # Give each process a moment to flush logs / close sockets.
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=2)
                log.info("[%s] terminated", t)
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] termination error: %s", t, exc)
        # Don't call sys.exit() inside a signal handler — let main() return.
        os._exit(0)

    def _handle_signal(signum, _frame):
        _is_signalled[0] = True
        cleanup(signum, _frame)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    atexit.register(cleanup, None, None)

    # Start streaming subprocess stdout IMMEDIATELY. If we wait until after
    # the health checks (as the previous version did), the api process
    # emits dozens of HuggingFace HEAD requests + sentence-transformers
    # logs during BGE warmup, the ~64KB PIPE buffer fills up, and the
    # child's stdout writes block — stalling warmup for ~80s and causing
    # wait_for to time out at 90s even though the server itself is healthy.
    # Streaming concurrently keeps the PIPE drained and lets the user see
    # live warmup progress.
    threads = [threading.Thread(target=stream_output, args=(p, t), daemon=True) for p, t in procs]
    for th in threads:
        th.start()

    # Give all a moment to bind.
    time.sleep(1.5)

    # Health checks (non-fatal: we keep running even if one is slow, the
    # user will see the error in the per-process logs).
    wait_for("http://127.0.0.1:8001/health", "mock", timeout=15)
    # API health check needs a generous timeout because the lifespan handler
    # pre-builds the default tenant's multi-agent (loads BGE embedding model
    # + compiles the langgraph) before /api/health returns 200. On a cold
    # start this takes 30-40s; give 90s headroom for slower machines.
    wait_for("http://127.0.0.1:8000/api/health", "api", timeout=90)
    if not skip_ecommerce:
        # The e-commerce service also does a schema init on startup; give it
        # 30s headroom (PostgreSQL schema creation is usually <2s but allow
        # for slow first-connection pool warmup).
        wait_for("http://127.0.0.1:8002/api/health", "shop", timeout=30)

    print("\n" + "=" * 60)
    print("  0719agent Commerce Platform is up:")
    print("    - Web UI (Agent chat):   http://127.0.0.1:8000/")
    print("    - API docs (Swagger):     http://127.0.0.1:8000/docs")
    print("    - Mock platform:          http://127.0.0.1:8001/health")
    if skip_ecommerce:
        print("    - E-commerce:             SKIPPED (ECOMMERCE_SKIP=1)")
    else:
        print("    - E-commerce platform:    http://127.0.0.1:8002/shop")
        print("    - E-commerce API docs:    http://127.0.0.1:8002/docs")
    print("    - Run end-to-end demo:    python scripts/demo.py")
    print("=" * 60 + "\n")

    try:
        for p, _ in procs:
            p.wait()
    except KeyboardInterrupt:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
