"""Runtime handles for a running gateway.

A :class:`LocalRun` represents a gateway that is currently serving traffic.
It's created by :meth:`envoyai.Gateway.local` (background subprocess) or by
:meth:`envoyai.Gateway.serve` (foreground), and it exposes the ports the
gateway is listening on plus a :meth:`stop` method.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = ["LocalRun"]


@dataclass
class LocalRun:
    """Handle for a running gateway.

    ``port`` is the HTTP port clients post to (OpenAI-compatible).
    ``admin_port`` is Envoy's admin endpoint. ``_proc`` and ``_config_path``
    are populated when the runtime started an ``aigw`` subprocess for us.
    """

    port: int = 1975
    admin_port: int = 1064
    _proc: Optional[subprocess.Popen[bytes]] = None
    _config_path: Optional[Path] = field(default=None, repr=False)

    @property
    def base_url(self) -> str:
        """OpenAI-compatible base URL for the running gateway."""
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        """Stop the underlying subprocess and clean up the temp config file.

        Safe to call repeatedly; no-op if the handle wasn't attached to a
        subprocess (e.g. when a gateway is running externally).
        """
        if self._proc is not None:
            from envoyai._internal.aigw_process import stop_background

            stop_background(self._proc)
            self._proc = None
        if self._config_path is not None:
            try:
                self._config_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._config_path = None
