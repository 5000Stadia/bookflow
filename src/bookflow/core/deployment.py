"""Deployment posture and fail-closed readiness; no networking or provisioning.

The local administrator selects posture in [host] config.toml while stopped.
Transport construction is deliberately separate from service activation: tests
can exercise a synthetic app, but no supported launcher bypasses preflight.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bookflow.core.config import Config
from bookflow.core.errors import BookflowError
from bookflow.core import public_origin as po


# One host process has one deployment posture. Offline use defaults to local.
_redact_diagnostics = False


def configure_diagnostics(deployment: "Deployment") -> None:
    """Set once during host/app construction, before serving requests."""
    global _redact_diagnostics
    _redact_diagnostics = deployment.wider


def redact_diagnostics() -> bool:
    """Whether host logs must omit exception bodies, facts and tracebacks."""
    return _redact_diagnostics


@dataclass(frozen=True)
class Blocker:
    code: str
    next_step: str


# These are evidence providers still to be built, never administrator attestations.
# Each later increment replaces its entries with checks of authoritative state.
PENDING = (
    Blocker("proxy_boundary", "Verify upstream isolation, HTTPS redirects, host-only HSTS and redacted proxy logs (increment 8)."),
    Blocker("permission_catalog", "Activate and verify the current permission catalog (increment 3)."),
    Blocker("projection_inventory", "Complete same-company authorization and publication evidence (increment 3)."),
    Blocker("cursor_lifecycle", "Install and verify bounded cursor rotation (increment 2)."),
    Blocker("session_bounds", "Verify idle and absolute session expiry (increment 4)."),
    Blocker("bearer_bounds", "Replace unbounded credentials and verify finite bearer lifetimes (increment 4)."),
    Blocker("login_limits", "Verify durable bounded authentication cooldowns (increment 4)."),
    Blocker("resource_limits", "Verify request, queue, report and transfer bounds (increment 4)."),
    Blocker("protected_storage", "Verify supported unlocked encrypted mounts and all sensitive paths (increment 5)."),
    Blocker("plaintext_residue", "Resolve retained plaintext and its approved disposition (increment 5)."),
    Blocker("encrypted_backups", "Enroll recipients and verify encrypted backup coverage (increment 6)."),
    Blocker("recovery_proof", "Verify independent recovery custody and a restore drill (increment 6)."),
    Blocker("operational_acceptance", "Complete deployment and recovery acceptance evidence (increment 8)."),
)


@dataclass(frozen=True)
class Deployment:
    profile: str = "local"
    public_origin: str | None = None
    bind: str | None = None
    trusted_proxy: str | None = None
    secure_cookies: bool = False

    def __post_init__(self):
        if self.profile not in ("local", "wider"):
            raise BookflowError("E_CONFIG_INVALID", message="Unknown deployment profile; use local or wider.")

    @classmethod
    def load(cls, root: Path, *, public_origin: str | None, bind: str | None, secure_cookies: bool):
        settings = Config.load(root / "config.toml").data["host"]
        return cls(settings.get("deployment_profile", "local"), public_origin, bind,
                   settings.get("trusted_proxy"), secure_cookies)

    @property
    def wider(self) -> bool:
        return self.profile == "wider"

    def transport_blockers(self) -> tuple[Blocker, ...]:
        if not self.wider:
            return ()
        found = []
        try:
            valid = po.normalize(self.public_origin) == self.public_origin and po.is_https(self.public_origin)
        except BookflowError:
            valid = False
        if not valid:
            found.append(Blocker("https_origin", "Configure one HTTPS public_origin without a path or credentials."))
        from bookflow.commands.host_cmds import parse_bind
        try:
            host, _ = parse_bind(self.bind if isinstance(self.bind, str) else "")
        except (BookflowError, ValueError):
            host = None
        if host != "127.0.0.1":
            found.append(Blocker("loopback_upstream", "Bind the upstream to 127.0.0.1:PORT."))
        if self.trusted_proxy != "127.0.0.1":
            found.append(Blocker("trusted_proxy", "Configure trusted_proxy = '127.0.0.1' and an owner-controlled local proxy."))
        if not self.secure_cookies:
            found.append(Blocker("secure_cookies", "Enable Secure browser cookies."))
        return tuple(found)

    def preflight(self) -> dict:
        blockers = (*self.transport_blockers(), *PENDING) if self.wider else ()
        return {"profile": self.profile, "wider_ready": False,
                "blockers": [{"code": b.code, "next_step": b.next_step} for b in blockers]}

    def require_transport(self) -> None:
        self._refuse(self.transport_blockers())

    def require_startup(self) -> None:
        if self.wider:
            self._refuse((*self.transport_blockers(), *PENDING))

    @staticmethod
    def _refuse(blockers) -> None:
        if blockers:
            raise BookflowError("E_SECURITY_NOT_READY", details={
                "wider_ready": False,
                "blockers": [{"code": b.code, "next_step": b.next_step} for b in blockers],
            })
