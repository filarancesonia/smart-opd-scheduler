"""Foundation: accounts, login, token refresh, role gating."""

from app.core.security import (
    Role,
    TokenError,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_password_hash_roundtrip():
    stored = hash_password("StrongPass123")
    assert stored != "StrongPass123"
    assert verify_password("StrongPass123", stored)
    assert not verify_password("wrong", stored)


def test_password_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_verify_rejects_garbage_hash():
    assert not verify_password("x", "not-a-real-hash")


def test_register_and_login(client):
    created = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "+91 98765 43210",
            "full_name": "Asha Patil",
            "password": "StrongPass123",
        },
    )
    assert created.status_code == 201, created.text
    # Phone is normalised to bare 10 digits regardless of how it was typed.
    assert created.json()["phone"] == "9876543210"
    assert created.json()["role"] == Role.PATIENT

    tokens = client.post(
        "/api/v1/auth/login",
        json={"phone": "9876543210", "password": "StrongPass123"},
    )
    assert tokens.status_code == 200
    assert tokens.json()["token_type"] == "bearer"


def test_duplicate_phone_rejected(client, register_user):
    register_user(phone="9876543210")
    again = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "9876543210",
            "full_name": "Someone Else",
            "password": "StrongPass123",
        },
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"


def test_invalid_phone_rejected(client):
    bad = client.post(
        "/api/v1/auth/register",
        json={"phone": "12345", "full_name": "X", "password": "StrongPass123"},
    )
    assert bad.status_code == 422


def test_wrong_password_does_not_leak_account_existence(client, register_user):
    register_user(phone="9876543210")
    known = client.post(
        "/api/v1/auth/login", json={"phone": "9876543210", "password": "nope-wrong"}
    )
    unknown = client.post(
        "/api/v1/auth/login", json={"phone": "9000000001", "password": "nope-wrong"}
    )
    assert known.status_code == unknown.status_code == 401
    assert known.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_me_requires_token(client, register_user):
    assert client.get("/api/v1/auth/me").status_code == 401

    _, headers = register_user()
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["full_name"] == "Test User"


def test_refresh_issues_new_access_token(client, register_user):
    register_user(phone="9876543210")
    login = client.post(
        "/api/v1/auth/login",
        json={"phone": "9876543210", "password": "StrongPass123"},
    ).json()

    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


def test_access_token_rejected_as_refresh_token(client, register_user):
    register_user(phone="9876543210")
    login = client.post(
        "/api/v1/auth/login",
        json={"phone": "9876543210", "password": "StrongPass123"},
    ).json()

    # An access token must not be usable to mint fresh credentials.
    reused = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["access_token"]}
    )
    assert reused.status_code == 401


def test_decode_rejects_tampered_token():
    token = create_access_token("1", Role.ADMIN)
    try:
        decode_token(token[:-4] + "AAAA")
    except TokenError:
        pass
    else:  # pragma: no cover
        raise AssertionError("tampered token was accepted")
