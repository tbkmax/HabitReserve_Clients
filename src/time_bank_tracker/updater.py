"""Check GitHub Releases for newer portable Windows builds."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from . import __version__
from .config import packaged_build_config


MAX_RELEASE_BYTES = 256 * 1024
DEFAULT_RELEASE_REPOSITORY = "tbkmax/HabitReserve_Clients"
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    release_notes_url: str


def release_repository() -> str:
    """Use the repository baked into a release; allow source-build testing."""
    configured = packaged_build_config().get("release_repository") or os.environ.get(
        "HABITRESERVE_RELEASE_REPOSITORY", DEFAULT_RELEASE_REPOSITORY
    )
    if not isinstance(configured, str) or not REPOSITORY_PATTERN.fullmatch(configured):
        raise UpdateError("The public desktop release repository is not configured.")
    return configured


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise UpdateError("The release has an invalid version number.")
    return tuple(int(part) for part in match.groups())


def is_newer(version: str, current: str = __version__) -> bool:
    return version_tuple(version) > version_tuple(current)


def _check_release_url(url: str, repository: str, version: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.path != f"/{repository}/releases/tag/v{version}"
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise UpdateError("The release contains an untrusted URL.")


def _open(url: str):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HabitReserve-Updater", "Accept": "application/vnd.github+json"},
    )
    return urllib.request.urlopen(request, timeout=20)


def fetch_release() -> Release:
    """Read the latest published release from the public GitHub API."""
    repository = release_repository()
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    try:
        with _open(url) as response:
            payload = response.read(MAX_RELEASE_BYTES + 1)
        if len(payload) > MAX_RELEASE_BYTES:
            raise UpdateError("The release information is too large.")
        document = json.loads(payload)
    except (OSError, ValueError) as error:
        raise UpdateError("Could not check GitHub Releases right now.") from error

    if not isinstance(document, dict):
        raise UpdateError("The release information is invalid.")
    tag = document.get("tag_name")
    notes_url = document.get("html_url")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("The release has an invalid version number.")
    version = tag[1:]
    version_tuple(version)
    if not isinstance(notes_url, str):
        raise UpdateError("The release information is incomplete.")
    _check_release_url(notes_url, repository, version)
    return Release(version, notes_url)
