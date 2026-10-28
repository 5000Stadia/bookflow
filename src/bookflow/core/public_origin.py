"""The host's public origin: the scheme://host[:port] people reach it at through a TLS proxy.

Bookflow can sit behind a TLS-terminating proxy (`colony serve`, Caddy, `tailscale serve`, nginx) that forwards
to a host bound on 127.0.0.1. The proxy's address is configuration, never guessed from a request: when it is set,
every absolute URL the host hands out (the printed address, setup and owner links, the MCP config `setup` writes)
uses it, the HTTP app takes it as the request's scheme and host, and Origin/Referer checks accept it. Nothing here
reads the Host or X-Forwarded-* headers. See design/https-serving.md.

The setting comes from `--public-origin` on `serve`, `setup` and `owner-link`, or from `[host] public_origin` in
the data root's config.toml; an explicit flag wins. Kept free of heavy imports: the CLI loads it at cold start.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from bookflow.core.errors import BookflowError

# The only address a host with a public origin listens on: the proxy in front of it reaches this, nothing else.
PROXY_BIND_HOST = "127.0.0.1"


def normalize(value: str | None, *, field: str = "public_origin") -> str | None:
    """`https://host[:port]` (or http), lower-cased, no trailing slash; None for empty. Refuses anything else."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    problem = None
    try:
        if any(ord(c) < 33 or ord(c) == 127 for c in text):
            raise ValueError("it contains spaces or control characters")
        parsed = urlsplit(text)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("it must start with https:// (or http://)")
        if not parsed.hostname:
            raise ValueError("it names no host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("it must not carry a user name or password")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("give the origin only (scheme, host and port), with no path, query or fragment")
        port = parsed.port
    except ValueError as e:
        problem = str(e) if str(e) and not str(e).startswith("Port") else "the port is not valid"
    if problem:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": problem}]})
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default else "")


def is_https(origin: str | None) -> bool:
    return bool(origin) and origin.startswith("https://")


def configured(data_root: Path | str) -> str | None:
    """`[host] public_origin` from config.toml, normalized; None when unset."""
    from bookflow.core.config import Config
    path = Path(data_root) / "config.toml"
    value = Config.load(path).data.get("host", {}).get("public_origin")
    return normalize(value, field="config.toml [host] public_origin") if value else None


def resolve(data_root: Path | str, explicit: str | None) -> str | None:
    """The explicit flag if given, else the configured one."""
    return normalize(explicit) if explicit else configured(data_root)


def base_url(origin: str | None, local_url: str) -> str:
    """The address handed to people and agents: the public origin when set, else the host's own local URL."""
    return origin + "/" if origin else local_url


def require_proxy_bind(origin: str | None, host_name: str, bind: str) -> None:
    """With a public origin the host listens on 127.0.0.1 only; the TLS proxy is what the network reaches."""
    if origin and host_name != PROXY_BIND_HOST:
        raise BookflowError("E_NETWORK_NOT_ALLOWED", details={"bind": bind, "public_origin": origin},
                            message=(f"With a public origin ({origin}) the host listens on {PROXY_BIND_HOST} only, "
                                     f"behind the TLS proxy that serves that origin; use --bind {PROXY_BIND_HOST}:PORT "
                                     "(and no --allow-network)."))


def local_origins(bind: str) -> set[str]:
    """The http origins a browser on this computer uses to reach the host directly at its bind address."""
    from bookflow.commands.host_cmds import host_url
    url = host_url(bind).rstrip("/")
    port = url.rsplit(":", 1)[1]
    found = {url}
    if url.startswith(("http://127.0.0.1:", "http://[::1]:")):
        found.add(f"http://localhost:{port}")
    return found


def origin_of(url: str | None) -> str | None:
    """The scheme://host[:port] of a URL such as a Referer, normalized; None when it has none."""
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        return normalize(f"{parsed.scheme}://{parsed.netloc}") if parsed.scheme and parsed.netloc else None
    except BookflowError:
        return None
