from __future__ import annotations

from .errors import AuraError

import os
import subprocess
import tempfile
import time
from pathlib import Path


# Chromium-based browsers can capture a page from the command line with no
# extra package installed, which keeps Aura dependency-free.
BROWSER_CANDIDATES = (
    "Google/Chrome/Application/chrome.exe",
    "Microsoft/Edge/Application/msedge.exe",
    "Chromium/Application/chrome.exe",
)
POSIX_BROWSERS = (
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
MIN_SCREENSHOT_BYTES = 1_000


class ScreenshotUnavailable(AuraError, RuntimeError):
    """Raised when no Chromium-based browser is installed to capture with."""


def find_browser() -> Path | None:
    """Locate an installed Chromium-based browser, or None."""
    if os.name == "nt":
        roots = [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")]
        for root in filter(None, roots):
            for relative in BROWSER_CANDIDATES:
                candidate = Path(root) / relative
                if candidate.is_file():
                    return candidate
        return None
    for candidate in POSIX_BROWSERS:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


def capture(url: str, destination: Path, *, width: int = 1200, height: int = 800,
            timeout: float = 25.0, browser: Path | None = None) -> Path:
    """Screenshot a page into `destination` using a local headless browser.

    The caller is responsible for ensuring `url` is a local address; this
    function never decides what may be visited.
    """
    executable = browser or find_browser()
    if executable is None:
        raise ScreenshotUnavailable(
            "Screenshots need Google Chrome, Microsoft Edge, or Chromium installed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with tempfile.TemporaryDirectory(prefix="aura-shot-") as profile:
        command = [
            str(executable), "--headless", "--disable-gpu", "--hide-scrollbars",
            f"--window-size={int(width)},{int(height)}",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            f"--screenshot={destination}", f"--user-data-dir={profile}", url,
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=creation_flags)
        deadline = time.monotonic() + max(5.0, float(timeout))
        try:
            while time.monotonic() < deadline:
                if destination.is_file() and destination.stat().st_size > MIN_SCREENSHOT_BYTES:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.2)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
    if not destination.is_file() or destination.stat().st_size <= MIN_SCREENSHOT_BYTES:
        raise ScreenshotUnavailable(
            "The browser did not produce a screenshot before the timeout.")
    return destination


def browser_command_preview(url: str, browser: Path | None = None) -> list[str]:
    """The command an approval prompt should show before anything launches."""
    executable = browser or find_browser()
    return [str(executable) if executable else "chrome", "--headless", "--screenshot", url]
