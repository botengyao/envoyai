"""Runtime handles for a running gateway.

A :class:`LocalRun` represents a gateway that is currently serving traffic.
It's created by :meth:`envoyai.Gateway.local` (background subprocess) or by
:meth:`envoyai.Gateway.serve` (foreground), and it exposes the ports the
gateway is listening on plus a :meth:`stop` method.

Today this module is intentionally small — it defines the handle shape and
a helper for tests. The actual ``aigw`` subprocess wrapper lands in a
follow-up commit; when it does, ``LocalRun`` will gain a ``subprocess.Popen``
field and a real ``stop`` implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["LocalRun"]


@dataclass
class LocalRun:
    """Handle for a running gateway.

    ``port`` is the HTTP port clients post to (OpenAI-compatible).
    ``admin_port`` is Envoy's admin endpoint (used for readiness probes and
    stats).

    ``_proc`` holds the underlying ``subprocess.Popen`` once the aigw
    integration lands; it's typed ``Any`` today so the runtime wrapper can
    be filled in without touching this file.
    """

    port: int = 1975
    admin_port: int = 1064
    _proc: Any = None

    @property
    def base_url(self) -> str:
        """OpenAI-compatible base URL for the running gateway."""
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        """Stop the underlying subprocess. No-op until the runtime lands."""
        proc = self._proc
        if proc is None:
            return
        # Placeholder — the subprocess wrapper in the next commit wires up
        # SIGTERM / wait / SIGKILL here.
        terminate = getattr(proc, "terminate", None)
        if callable(terminate):
            terminate()
