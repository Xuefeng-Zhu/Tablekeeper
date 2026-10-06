"""Build narrow Codex 0.160 profiles without changing host configuration.

The app server and BAND adapter must omit legacy sandbox/sandboxPolicy fields
to inherit this profile. Constructing arguments is not live permission proof.
"""
from __future__ import annotations

import json
from pathlib import Path
import re


_PROFILE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _toml(value: str | bool | dict) -> str:
    """Serialize the limited inline-table shape as one literal argv value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return "{" + ",".join(f"{_toml(key)}={_toml(item)}" for key, item in sorted(value.items())) + "}"


def profile_arguments(
    name: str,
    domains: list[str] | tuple[str, ...],
    *,
    allow_local_binding: bool = False,
    unix_sockets: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Return explicit -c arguments for an exact host/socket allowlist.

    No sockets, extra writable roots, or local listeners are granted by default.
    A socket can confer the server's privileges; review that boundary before
    passing it here. These arguments never bypass managed permission policy.
    """
    if not isinstance(name, str) or not _PROFILE.fullmatch(name):
        raise ValueError("Permission profile name must contain lowercase letters, digits, underscores or hyphens and start with a letter")
    if not isinstance(allow_local_binding, bool):
        raise ValueError("allow_local_binding must be an explicit boolean")
    if not isinstance(domains, (list, tuple)) or not domains:
        raise ValueError("At least one exact allowed domain is required")
    hosts = set()
    for domain in domains:
        if not isinstance(domain, str) or len(domain) > 253 or not _DOMAIN.fullmatch(domain.lower()):
            raise ValueError("Domains must be exact host names or IPv4 addresses without wildcards, schemes, ports or paths")
        hosts.add(domain.lower())
    if allow_local_binding and not {"127.0.0.1", "localhost"}.issubset(hosts):
        raise ValueError("Local binding requires explicit localhost and 127.0.0.1 allowlist entries")
    if not isinstance(unix_sockets, (list, tuple)):
        raise ValueError("unix_sockets must be a list or tuple of explicit absolute socket paths")
    sockets = set()
    for path in unix_sockets:
        if not isinstance(path, str) or not path or any(character in path for character in ("*", "?", "\x00", "\n", "\r")):
            raise ValueError("Socket entries must be literal absolute paths without wildcards or control characters")
        parsed = Path(path)
        if not parsed.is_absolute() or ".." in parsed.parts or str(parsed) != path:
            raise ValueError("Socket entries must be normalized absolute paths")
        sockets.add(path)
    network: dict = {
        "enabled": True,
        "allow_local_binding": allow_local_binding,
        "domains": {host: "allow" for host in sorted(hosts)},
    }
    if sockets:
        network["unix_sockets"] = {path: "allow" for path in sorted(sockets)}
    profile = {
        "extends": ":workspace",
        "filesystem": {":workspace_roots": {".git": "write"}},
        "network": network,
    }
    return [
        "-c", f"permissions.{name}={_toml(profile)}",
        "-c", f"default_permissions={_toml(name)}",
        "-c", "features.network_proxy=true",
        "-c", 'approval_policy="never"',
    ]
