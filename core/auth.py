"""M41 — Authentication and Principal Resolution for Project AURA.

Provides authentication abstraction, TokenAuthenticator with constant-time token comparison,
expiration validation, and principal resolution.
"""

from __future__ import annotations

import hmac
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from core.identity import UserIdentity, UserRole, UserScope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthenticationResult:
    """Result of an authentication attempt."""

    success: bool
    identity: UserIdentity | None = None
    error_message: str | None = None
    status_code: int = 200


class BaseAuthenticator(ABC):
    """Abstract interface for authenticating requests and resolving UserIdentity."""

    @abstractmethod
    def authenticate(self, credentials: str | None, **kwargs: Any) -> AuthenticationResult:
        """Authenticate provided credentials (e.g. bearer token, API key) and return AuthenticationResult."""
        pass


class TokenAuthenticator(BaseAuthenticator):
    """Bearer/API-Token authenticator using hmac.compare_digest for secret comparison.
    
    Maintains a mapping of token secrets to UserIdentity principals, optionally backed
    by a persistent BaseApiTokenRepository.
    Supports registration, revocation, and expiration validation.
    """

    def __init__(
        self,
        initial_tokens: dict[str, UserIdentity] | None = None,
        token_repo: Any | None = None,
    ) -> None:
        self._tokens: dict[str, UserIdentity] = dict(initial_tokens or {})
        self._token_repo = token_repo

    def register_token(self, token: str, identity: UserIdentity) -> None:
        """Register a token mapped to a UserIdentity."""
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Token must be a non-empty string.")
        if not isinstance(identity, UserIdentity):
            raise TypeError("identity must be an instance of UserIdentity.")

        if self._token_repo is not None:
            self._token_repo.register_token(
                raw_token=token.strip(),
                user_id=identity.user_id,
            )
        self._tokens[token] = identity

    def revoke_token(self, token: str) -> bool:
        """Revoke a registered token. Returns True if token was found and removed."""
        found = False
        if self._token_repo is not None:
            found = self._token_repo.revoke_token(token)

        to_delete: list[str] = []
        for registered_token in list(self._tokens.keys()):
            if hmac.compare_digest(registered_token, token):
                to_delete.append(registered_token)
                found = True
        for k in to_delete:
            self._tokens.pop(k, None)
        return found

    def authenticate(self, credentials: str | None, **kwargs: Any) -> AuthenticationResult:
        """Validate token credentials and resolve the associated UserIdentity.
        
        Uses hmac.compare_digest for secret comparison and queries token repository if configured.
        Checks credential expiration.
        """
        if credentials is None or not isinstance(credentials, str) or not credentials.strip():
            return AuthenticationResult(
                success=False,
                identity=None,
                error_message="Missing authentication credentials.",
                status_code=401,
            )

        token = credentials.strip()

        # Check repository if configured
        if self._token_repo is not None:
            res = self._token_repo.find_by_token(token)
            if res is not None:
                token_id, identity = res
                if identity.is_expired():
                    return AuthenticationResult(
                        success=False,
                        identity=None,
                        error_message="Authentication token has expired.",
                        status_code=401,
                    )
                return AuthenticationResult(
                    success=True,
                    identity=identity,
                    status_code=200,
                )

        matched_identity: UserIdentity | None = None

        # Constant-time comparison across all registered in-memory tokens
        for reg_token, ident in self._tokens.items():
            if hmac.compare_digest(reg_token, token):
                matched_identity = ident
                break

        if matched_identity is None:
            return AuthenticationResult(
                success=False,
                identity=None,
                error_message="Invalid authentication token.",
                status_code=401,
            )

        if matched_identity.is_expired():
            return AuthenticationResult(
                success=False,
                identity=None,
                error_message="Authentication token has expired.",
                status_code=401,
            )

        return AuthenticationResult(
            success=True,
            identity=matched_identity,
            status_code=200,
        )


def create_token_authenticator(
    master_key: str | None = None,
    additional_tokens: dict[str, UserIdentity] | None = None,
    token_repo: Any | None = None,
) -> TokenAuthenticator:
    """Factory to create a TokenAuthenticator with an optional master API key, token repository, and additional tokens."""
    auth = TokenAuthenticator(token_repo=token_repo)
    if master_key and master_key.strip():
        admin_identity = UserIdentity(
            user_id="aura_admin",
            username="Administrator",
            roles=frozenset({UserRole.ADMIN, UserRole.USER}),
            scopes=frozenset(s.value for s in UserScope),
            is_authenticated=True,
        )
        auth.register_token(master_key.strip(), admin_identity)

    if additional_tokens:
        for tok, ident in additional_tokens.items():
            auth.register_token(tok, ident)

    return auth
