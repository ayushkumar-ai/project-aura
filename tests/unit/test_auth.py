import time
import pytest
from core.identity import UserIdentity, UserRole, UserScope
from core.auth import (
    AuthenticationResult,
    TokenAuthenticator,
    create_token_authenticator,
)


def test_token_authenticator_basic():
    auth = TokenAuthenticator()
    user_ident = UserIdentity(user_id="alice_01", username="Alice")
    auth.register_token("token_alice_secret", user_ident)

    # Valid token
    res = auth.authenticate("token_alice_secret")
    assert res.success
    assert res.identity is not None
    assert res.identity.user_id == "alice_01"
    assert res.status_code == 200

    # Invalid token
    res_bad = auth.authenticate("wrong_token")
    assert not res_bad.success
    assert res_bad.identity is None
    assert res_bad.status_code == 401

    # Empty token
    res_empty = auth.authenticate("")
    assert not res_empty.success
    assert res_empty.status_code == 401


def test_token_authenticator_expiration():
    now = time.time()
    auth = TokenAuthenticator()
    expired_ident = UserIdentity(
        user_id="bob_01",
        username="Bob",
        expires_at=now - 50,
    )
    auth.register_token("expired_token", expired_ident)

    res = auth.authenticate("expired_token")
    assert not res.success
    assert "expired" in res.error_message.lower()
    assert res.status_code == 401


def test_token_revocation():
    auth = TokenAuthenticator()
    ident = UserIdentity(user_id="carol_01", username="Carol")
    auth.register_token("carol_secret_123", ident)

    assert auth.authenticate("carol_secret_123").success

    revoked = auth.revoke_token("carol_secret_123")
    assert revoked
    assert not auth.authenticate("carol_secret_123").success


def test_create_token_authenticator_factory():
    auth = create_token_authenticator(
        master_key="master_admin_secret_xyz",
        additional_tokens={
            "user_tok_1": UserIdentity(user_id="user_1", username="User 1"),
        },
    )

    # Check master key
    res_admin = auth.authenticate("master_admin_secret_xyz")
    assert res_admin.success
    assert res_admin.identity.has_role(UserRole.ADMIN)

    # Check additional token
    res_user = auth.authenticate("user_tok_1")
    assert res_user.success
    assert res_user.identity.user_id == "user_1"
