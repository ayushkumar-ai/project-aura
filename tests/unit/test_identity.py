import time
import pytest
from core.identity import (
    UserIdentity,
    UserRole,
    UserScope,
    create_anonymous_identity,
    create_dev_identity,
)


def test_user_identity_creation_and_immutability():
    ident = UserIdentity(
        user_id="user_123",
        username="alice",
        roles=frozenset({UserRole.USER}),
        scopes=frozenset({UserScope.RUN.value, UserScope.READ_STATE.value}),
        is_authenticated=True,
    )
    assert ident.user_id == "user_123"
    assert ident.username == "alice"
    assert ident.has_role(UserRole.USER)
    assert not ident.has_role(UserRole.ADMIN)
    assert ident.has_scope(UserScope.RUN.value)
    assert not ident.has_scope(UserScope.ADMIN.value)

    # Immutability
    with pytest.raises(Exception):
        ident.user_id = "user_456"


def test_admin_role_has_all_roles_and_scopes():
    admin = UserIdentity(
        user_id="admin_01",
        username="admin",
        roles=frozenset({UserRole.ADMIN}),
        scopes=frozenset(),
        is_authenticated=True,
    )
    assert admin.has_role(UserRole.USER)
    assert admin.has_role(UserRole.ADMIN)
    assert admin.has_scope("aura:run")
    assert admin.has_scope("aura:admin")
    assert admin.has_scope("any_arbitrary_scope")


def test_identity_expiration():
    now = time.time()
    valid_ident = UserIdentity(user_id="u1", username="u1", expires_at=now + 100)
    assert not valid_ident.is_expired(now=now)

    expired_ident = UserIdentity(user_id="u2", username="u2", expires_at=now - 10)
    assert expired_ident.is_expired(now=now)

    no_expiry_ident = UserIdentity(user_id="u3", username="u3", expires_at=None)
    assert not no_expiry_ident.is_expired(now=now)


def test_identity_serialization():
    ident = UserIdentity(
        user_id="u_serial",
        username="bob",
        roles=frozenset({UserRole.OPERATOR}),
        scopes=frozenset({UserScope.TASK.value}),
        is_authenticated=True,
        metadata={"dept": "engineering"},
    )
    data = ident.to_dict()
    assert data["user_id"] == "u_serial"
    assert UserRole.OPERATOR.value in data["roles"]
    assert UserScope.TASK.value in data["scopes"]
    assert data["metadata"]["dept"] == "engineering"

    restored = UserIdentity.from_dict(data)
    assert restored.user_id == ident.user_id
    assert restored.username == ident.username
    assert restored.has_role(UserRole.OPERATOR)
    assert restored.has_scope(UserScope.TASK.value)
    assert restored.metadata == ident.metadata


def test_anonymous_and_dev_factories():
    anon = create_anonymous_identity("anon_99")
    assert not anon.is_authenticated
    assert anon.has_role(UserRole.ANONYMOUS)
    assert not anon.has_scope(UserScope.RUN.value)

    dev = create_dev_identity("dev_01", "Developer")
    assert dev.is_authenticated
    assert dev.has_role(UserRole.ADMIN)
    assert dev.has_scope(UserScope.DEVICE_EXEC.value)
