from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_SERVER_URL = "https://localhost"
BUILD_CONFIG_NAME = "_habitreserve_build.json"
LEGACY_BUILD_CONFIG_NAME = "_time_bank_build.json"


def packaged_build_config() -> dict:
    """Read release-only settings embedded in a frozen desktop build."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if not bundle_root:
        return {}
    for name in (BUILD_CONFIG_NAME, LEGACY_BUILD_CONFIG_NAME):
        try:
            payload = json.loads((Path(bundle_root) / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _packaged_server_url() -> str | None:
    """Read the immutable server origin bundled by the release build."""
    configured = packaged_build_config().get("server_url")
    if not isinstance(configured, str) or not configured.strip():
        return None
    return configured.strip().rstrip("/")


def server_url() -> str:
    """Return the deployment-controlled server URL."""
    configured = (
        os.environ.get("HABITRESERVE_SERVER_URL", "").strip()
        or os.environ.get("TIME_BANK_SERVER_URL", "").strip()
    )
    return (configured or _packaged_server_url() or DEFAULT_SERVER_URL).rstrip("/")


def tls_ca_file(url: str) -> str | None:
    """Return an optional additional CA without disabling TLS verification.

    Production HTTPS uses the normal trust store. A source checkout pointed at
    local Compose may automatically add the ignored development CA.
    """
    configured = (
        os.environ.get("HABITRESERVE_TLS_CA_FILE", "").strip()
        or os.environ.get("TIME_BANK_TLS_CA_FILE", "").strip()
    )
    if configured:
        return configured

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return None

    anchors = (
        Path(sys.executable).resolve().parent,
        Path.cwd().resolve(),
        Path(__file__).resolve().parent,
    )
    checked: set[Path] = set()
    for anchor in anchors:
        for root in (anchor, *anchor.parents[:4]):
            if root in checked:
                continue
            checked.add(root)
            candidate = root / "deploy" / "nginx" / "certs" / "dev-ca.crt"
            if candidate.is_file():
                return str(candidate)
    return None
