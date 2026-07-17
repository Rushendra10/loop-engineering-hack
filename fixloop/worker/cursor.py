"""Small adapter around Cursor's headless CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .errors import WorkerError


class CursorRunner:
    def __init__(self, binary: str | None = None, model: str | None = None):
        self.binary = binary or os.environ.get("FIXLOOP_CURSOR_BIN") or self._find_binary()
        # `auto` works on Cursor's free tier. Deployments can select another
        # account-enabled model through FIXLOOP_CURSOR_MODEL.
        self.model = model or os.environ.get("FIXLOOP_CURSOR_MODEL", "auto")

    @staticmethod
    def _find_binary() -> str:
        candidates = [
            shutil.which("cursor-agent"),
            str(Path.home() / ".local" / "bin" / "cursor-agent"),
            str(Path.home() / ".cursor" / "bin" / "cursor-agent"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        raise WorkerError("CURSOR_UNAVAILABLE", "cursor-agent is not installed")

    def __call__(self, repo: Path, prompt: str, timeout: float, phase: str) -> None:
        command = [
            self.binary,
            "-p",
            "--force",
            "--trust",
            "--model",
            self.model,
            "--output-format",
            "stream-json",
            prompt,
        ]
        try:
            proc = subprocess.run(
                command,
                cwd=repo,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                timeout=max(1, timeout),
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerError("CURSOR_TIMEOUT", f"Cursor timed out during {phase}") from exc
        except OSError as exc:
            raise WorkerError("CURSOR_UNAVAILABLE", str(exc)) from exc

        terminal_success = False
        parsed = 0
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            parsed += 1
            if event.get("type") == "result" and event.get("subtype") == "success" and not event.get("is_error"):
                terminal_success = True
        if proc.returncode != 0:
            error = proc.stderr.casefold()
            if "not authenticated" in error or "not logged in" in error:
                raise WorkerError(
                    "CURSOR_AUTH_MISSING",
                    "run cursor-agent login locally or provide CURSOR_API_KEY",
                )
            if "cannot use this model" in error or "named models unavailable" in error:
                raise WorkerError(
                    "CURSOR_MODEL_UNAVAILABLE",
                    f"model {self.model!r} is not enabled for this Cursor account",
                )
            raise WorkerError("CURSOR_FAILED", f"Cursor failed during {phase} (exit {proc.returncode})")
        if not parsed or not terminal_success:
            raise WorkerError("CURSOR_MALFORMED_OUTPUT", f"Cursor returned no successful result event during {phase}")
