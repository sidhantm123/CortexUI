"""
Is a newer CortexUI on GitHub? Used by Help → Check for Updates and the automatic check at launch.

One anonymous request to the GitHub API (no data about the user or their recordings is sent). The
check never installs anything: it offers the download for this machine, opened in the browser.
"""
import json
import platform
import re
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from version import APP_URL, APP_VERSION

API_URL = APP_URL.replace("https://github.com/", "https://api.github.com/repos/")
TIMEOUT_S = 10.0

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z]+)\.?(\d+)?)?$")

# The download for each machine, by the end of its file name (see packaging/make_dmg.sh and the workflow)
WINDOWS_ASSET = "-Windows-x64.zip"
MAC_ASSETS = {True: "-macOS-AppleSilicon.dmg", False: "-macOS-Intel.dmg"}


@dataclass
class Release:
    version: str  # "1.1.0" or "1.1.0-beta.1"
    page: str  # the release page (notes and every file)
    assets: Dict[str, str]  # file name -> download URL
    prerelease: bool


def parse_version(text: str) -> Optional[Tuple]:
    """
    "1.1.0" or "v1.1.0-beta.2" as a tuple that sorts by version, a release above its betas
    (1.1.0-beta.2 < 1.1.0 < 1.1.1). None if it isn't a version.
    """
    m = _VERSION.match(text.strip())
    if not m:
        return None
    major, minor, patch, label, number = m.groups()
    final = label is None
    return int(major), int(minor), int(patch), int(final), (label or "").lower(), int(number or 0)


def is_newer(candidate: str, current: Optional[str] = None) -> bool:
    """candidate is a later version than current (default: this app's)."""
    a, b = parse_version(candidate), parse_version(current or APP_VERSION)
    return a is not None and b is not None and a > b


def _release(item: dict) -> Optional[Release]:
    version = (item.get("tag_name") or "").lstrip("v")
    if item.get("draft") or parse_version(version) is None:
        return None
    assets = {a["name"]: a["browser_download_url"] for a in item.get("assets", [])
              if a.get("name") and a.get("browser_download_url")}
    return Release(version, item.get("html_url") or f"{APP_URL}/releases", assets, bool(item.get("prerelease")))


def newest_release(items, include_prereleases: bool = False) -> Optional[Release]:
    """The newest published release in the API's JSON (one release or a list)."""
    if isinstance(items, dict):
        items = [items]
    releases = [r for r in map(_release, items) if r and (include_prereleases or not r.prerelease)]
    return max(releases, key=lambda r: parse_version(r.version), default=None)


def fetch_newest_release(include_prereleases: bool = False) -> Optional[Release]:
    """
    Ask GitHub for the newest release; None if it can't be reached or answers nonsense. Betas
    are included only for a beta build (the stable "latest" never points at one).
    """
    url = f"{API_URL}/releases?per_page=20" if include_prereleases else f"{API_URL}/releases/latest"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"CortexUI/{APP_VERSION}",  # GitHub refuses requests without one
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S, context=ssl_context()) as response:
            return newest_release(json.load(response), include_prereleases)
    except Exception:
        return None


def ssl_context() -> ssl.SSLContext:
    """HTTPS settings with certifi's root certificates: a python.org Python (the packaged app) has none of its own."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def asset_for(assets: Dict[str, str], system: str, apple_silicon: bool) -> Optional[str]:
    """Download URL of the file for this machine (system as sys.platform), or None."""
    if system == "win32":
        suffix = WINDOWS_ASSET
    elif system == "darwin":
        suffix = MAC_ASSETS[apple_silicon]
    else:
        return None
    return next((url for name, url in assets.items() if name.endswith(suffix)), None)


def asset_for_this_machine(assets: Dict[str, str]) -> Optional[str]:
    return asset_for(assets, sys.platform, _apple_silicon())


def _apple_silicon() -> bool:
    """An Apple silicon Mac, also when this app is the Intel build running under Rosetta."""
    if sys.platform != "darwin":
        return False
    if platform.machine() == "arm64":
        return True
    try:
        out = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True, timeout=2)
        return out.stdout.strip() == "1"
    except Exception:
        return False
