import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db

# Create an in-memory SQLite database for isolated test execution
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_database():
    """Create a fresh database schema for every test and tear it down afterwards."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client():
    """FastAPI TestClient with overridden get_db dependency pointing to the test DB."""
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==========================================
# 1. Root / Health Check Endpoint Tests
# ==========================================

def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data == {"message": "Welcome to ChatConnect API"}


# ==========================================
# 2. Registration Endpoint Tests
# ==========================================

def test_register_success(client):
    payload = {"username": "alice", "password": "Password123!"}
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "user" in data
    assert data["user"]["username"] == "alice"
    assert "id" in data["user"]
    assert "created_at" in data["user"]
    # Verify hashed password is NEVER returned in response
    assert "password" not in data["user"]
    assert "hashed_password" not in data["user"]


def test_register_duplicate_username(client):
    payload = {"username": "alice", "password": "Password123!"}
    client.post("/api/auth/register", json=payload)

    # Attempt to register again with same username
    duplicate_res = client.post("/api/auth/register", json=payload)
    assert duplicate_res.status_code == 400
    assert duplicate_res.json()["detail"] == "Username already registered"


def test_register_missing_fields(client):
    # Missing password
    res = client.post("/api/auth/register", json={"username": "bob"})
    assert res.status_code == 422

    # Empty payload
    res = client.post("/api/auth/register", json={})
    assert res.status_code == 422


# ==========================================
# 3. Login Endpoint Tests
# ==========================================

def test_login_success(client):
    # First register user
    client.post("/api/auth/register", json={"username": "carol", "password": "SecretPassword1"})

    # Now login
    res = client.post("/api/auth/login", json={"username": "carol", "password": "SecretPassword1"})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["username"] == "carol"


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={"username": "dave", "password": "RealPassword123"})

    res = client.post("/api/auth/login", json={"username": "dave", "password": "WrongPassword"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Incorrect username or password"


def test_login_nonexistent_user(client):
    res = client.post("/api/auth/login", json={"username": "ghost", "password": "SomePassword"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Incorrect username or password"


# ==========================================
# 4. Current User (/me) Endpoint Tests
# ==========================================

def test_get_me_success(client):
    reg = client.post("/api/auth/register", json={"username": "eve", "password": "Password123"})
    token = reg.json()["access_token"]

    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "eve"
    assert "id" in data


def test_get_me_missing_token(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_get_me_invalid_token(client):
    res = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid_token_xyz"})
    assert res.status_code == 401


# ==========================================
# 5. List Users (/users) Endpoint Tests
# ==========================================

def test_get_users_success(client):
    # Register multiple users
    reg_user1 = client.post("/api/auth/register", json={"username": "user1", "password": "Password123"})
    token1 = reg_user1.json()["access_token"]

    client.post("/api/auth/register", json={"username": "user2", "password": "Password123"})
    client.post("/api/auth/register", json={"username": "user3", "password": "Password123"})

    # Fetch users as user1: should return user2 and user3, excluding user1
    res = client.get("/api/auth/users", headers={"Authorization": f"Bearer {token1}"})
    assert res.status_code == 200
    users = res.json()
    assert len(users) == 2
    usernames = [u["username"] for u in users]
    assert "user2" in usernames
    assert "user3" in usernames
    assert "user1" not in usernames


def test_get_users_unauthorized(client):
    res = client.get("/api/auth/users")
    assert res.status_code == 401
