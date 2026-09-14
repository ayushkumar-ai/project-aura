"""M41 — Canonical Identity and Principal Representation for Project AURA.

Defines authenticated principals, user roles, permission scopes, and lifecycle validation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"
    ANONYMOUS = "anonymous"


class UserScope(str, Enum):
    RUN = "aura:run"
    TASK = "aura:task"
    READ_STATE = "aura:state:read"
    WRITE_STATE = "aura:state:write"
    ADMIN = "aura:admin"
    SYSTEM_SYNC = "aura:system:sync"
    DEVICE_EXEC = "aura:device:exec"


@dataclass(frozen=True)
class UserIdentity:
    """Immutable representation of an authenticated or unauthenticated security principal."""

    user_id: str
    username: str
    roles: frozenset[UserRole] = field(default_factory=lambda: frozenset({UserRole.USER}))
    scopes: frozenset[str] = field(default_factory=lambda: frozenset({UserScope.RUN.value, UserScope.READ_STATE.value, UserScope.WRITE_STATE.value}))
    is_authenticated: bool = True
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValueError("user_id must be a non-empty string.")
        if not isinstance(self.username, str) or not self.username.strip():
            object.__setattr__(self, "username", self.user_id)
        if not isinstance(self.roles, (set, frozenset)):
            raise TypeError("roles must be a set or frozenset of UserRole.")
        if not isinstance(self.scopes, (set, frozenset)):
            raise TypeError("scopes must be a set or frozenset of strings.")

        # Ensure types are frozenset for immutability
        if not isinstance(self.roles, frozenset):
            object.__setattr__(self, "roles", frozenset(self.roles))
        if not isinstance(self.scopes, frozenset):
            object.__setattr__(self, "scopes", frozenset(self.scopes))

    def has_role(self, role: UserRole | str) -> bool:
        """Check if identity possesses a given role."""
        r = UserRole(role) if isinstance(role, str) else role
        return r in self.roles or UserRole.ADMIN in self.roles

    def has_scope(self, scope: str) -> bool:
        """Check if identity possesses a given permission scope."""
        if not self.is_authenticated:
            return False
        if UserRole.ADMIN in self.roles or UserScope.ADMIN.value in self.scopes:
            return True
        return scope in self.scopes

    def is_expired(self, now: float | None = None) -> bool:
        """Check whether the identity's credential expiration timestamp has passed."""
        if self.expires_at is None:
            return False
        current_time = now if now is not None else time.time()
        return current_time > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serialize identity to dictionary."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "roles": [r.value for r in self.roles],
            "scopes": list(self.scopes),
            "is_authenticated": self.is_authenticated,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserIdentity:
        """Reconstruct identity from serialized dictionary."""
        roles = frozenset(UserRole(r) for r in data.get("roles", [UserRole.USER.value]))
        scopes = frozenset(data.get("scopes", []))
        return cls(
            user_id=data["user_id"],
            username=data.get("username", data["user_id"]),
            roles=roles,
            scopes=scopes,
            is_authenticated=data.get("is_authenticated", True),
            created_at=data.get("created_at", time.time()),
            expires_at=data.get("expires_at"),
            metadata=data.get("metadata", {}),
        )


def create_anonymous_identity(user_id: str = "anonymous") -> UserIdentity:
    """Construct an explicitly unauthenticated anonymous identity."""
    return UserIdentity(
        user_id=user_id,
        username="Anonymous",
        roles=frozenset({UserRole.ANONYMOUS}),
        scopes=frozenset(),
        is_authenticated=False,
    )


def create_dev_identity(user_id: str = "dev_user", username: str = "Developer") -> UserIdentity:
    """Construct a full-access development/test identity (strictly for non-production environments)."""
    return UserIdentity(
        user_id=user_id,
        username=username,
        roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scopes=frozenset(s.value for s in UserScope),
        is_authenticated=True,
    )
