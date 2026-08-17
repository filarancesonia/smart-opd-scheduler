"""Shared test fixtures: an isolated in-memory database per test."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import create_app

# Production hashing cost would add minutes to a suite that registers hundreds
# of accounts. Correctness of the hash format is covered in test_identity.py.
settings.scrypt_cost_log2 = 11


@pytest.fixture
def db_session():
    # StaticPool keeps every connection pointed at the same in-memory database.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models  # noqa: F401  (registers all tables)

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def register_user(client):
    """Create an account and return (user_json, auth_headers)."""

    def _register(
        phone: str = "9876543210",
        role: str = "patient",
        password: str = "StrongPass123",
        full_name: str = "Test User",
    ):
        created = client.post(
            "/api/v1/auth/register",
            json={
                "phone": phone,
                "full_name": full_name,
                "password": password,
                "role": role,
            },
        )
        assert created.status_code == 201, created.text
        tokens = client.post(
            "/api/v1/auth/login", json={"phone": phone, "password": password}
        )
        assert tokens.status_code == 200, tokens.text
        access = tokens.json()["access_token"]
        return created.json(), {"Authorization": f"Bearer {access}"}

    return _register


@pytest.fixture
def admin(register_user):
    """An administrator account: (user_json, auth_headers)."""
    return register_user(phone="9000000001", role="admin", full_name="Admin One")


@pytest.fixture
def department(client, admin):
    _, headers = admin
    resp = client.post(
        "/api/v1/departments",
        json={"name": "General Medicine", "code": "gm", "floor": "2"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def doctor(client, admin, department, register_user):
    """A doctor with a profile: (doctor_json, that doctor's auth_headers)."""
    _, admin_headers = admin
    doc_user, doc_headers = register_user(
        phone="9000000002", role="doctor", full_name="Dr. Sharma"
    )
    resp = client.post(
        "/api/v1/doctors",
        json={
            "user_id": doc_user["id"],
            "department_id": department["id"],
            "registration_no": "MH-2019-45231",
            "specialisation": "Internal Medicine",
            "avg_consultation_minutes": 10,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json(), doc_headers
