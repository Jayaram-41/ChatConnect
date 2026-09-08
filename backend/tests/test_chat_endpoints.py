import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client():
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

def test_create_and_get_conversation(client):
    # 1. Register two users
    res1 = client.post("/api/auth/register", json={"username": "user1", "password": "Password123!"})
    token1 = res1.json()["access_token"]
    user1_id = res1.json()["user"]["id"]

    res2 = client.post("/api/auth/register", json={"username": "user2", "password": "Password123!"})
    token2 = res2.json()["access_token"]
    user2_id = res2.json()["user"]["id"]

    # 2. Create conversation from user1 to user2
    headers1 = {"Authorization": f"Bearer {token1}"}
    conv_res = client.post("/api/conversations", json={"target_user_id": user2_id}, headers=headers1)
    assert conv_res.status_code == 200
    conv_data = conv_res.json()
    conv_id = conv_data["id"]
    assert conv_id > 0
    assert len(conv_data["participants"]) == 2

    # 3. Create conversation again - should return the existing conversation
    conv_res2 = client.post("/api/conversations", json={"target_user_id": user2_id}, headers=headers1)
    assert conv_res2.status_code == 200
    assert conv_res2.json()["id"] == conv_id

    # 4. Same from user2 to user1 - should return the same conversation
    headers2 = {"Authorization": f"Bearer {token2}"}
    conv_res3 = client.post("/api/conversations", json={"target_user_id": user1_id}, headers=headers2)
    assert conv_res3.status_code == 200
    assert conv_res3.json()["id"] == conv_id

    # 5. List conversations for user1
    my_convs = client.get("/api/conversations", headers=headers1)
    assert my_convs.status_code == 200
    assert len(my_convs.json()) == 1

def test_create_conversation_errors(client):
    res1 = client.post("/api/auth/register", json={"username": "user1", "password": "Password123!"})
    token1 = res1.json()["access_token"]
    user1_id = res1.json()["user"]["id"]
    headers1 = {"Authorization": f"Bearer {token1}"}

    # Attempt to chat with oneself
    err_self = client.post("/api/conversations", json={"target_user_id": user1_id}, headers=headers1)
    assert err_self.status_code == 400

    # Attempt to chat with non-existent user
    err_none = client.post("/api/conversations", json={"target_user_id": 99999}, headers=headers1)
    assert err_none.status_code == 404

def test_send_and_get_messages(client):
    res1 = client.post("/api/auth/register", json={"username": "sender_user", "password": "Password123!"})
    token1 = res1.json()["access_token"]
    
    res2 = client.post("/api/auth/register", json={"username": "receiver_user", "password": "Password123!"})
    token2 = res2.json()["access_token"]
    user2_id = res2.json()["user"]["id"]
    
    res3 = client.post("/api/auth/register", json={"username": "outsider_user", "password": "Password123!"})
    token3 = res3.json()["access_token"]

    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}
    headers3 = {"Authorization": f"Bearer {token3}"}

    # Create conversation
    conv = client.post("/api/conversations", json={"target_user_id": user2_id}, headers=headers1).json()
    conv_id = conv["id"]

    # Send message from user1
    msg_res = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "Hello receiver!"},
        headers=headers1
    )
    assert msg_res.status_code == 200
    msg_data = msg_res.json()
    assert msg_data["content"] == "Hello receiver!"
    assert msg_data["conversation_id"] == conv_id

    # Send reply from user2
    msg_res2 = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "Hey sender, got your message!"},
        headers=headers2
    )
    assert msg_res2.status_code == 200

    # User 1 fetches message history
    history1 = client.get(f"/api/conversations/{conv_id}/messages", headers=headers1)
    assert history1.status_code == 200
    messages = history1.json()
    assert len(messages) == 2
    assert messages[0]["content"] == "Hello receiver!"
    assert messages[1]["content"] == "Hey sender, got your message!"

    # User 2 fetches message history
    history2 = client.get(f"/api/conversations/{conv_id}/messages", headers=headers2)
    assert history2.status_code == 200
    assert len(history2.json()) == 2

    # Outsider user (user3) attempts to access conversation history -> 403 Forbidden
    outsider_get = client.get(f"/api/conversations/{conv_id}/messages", headers=headers3)
    assert outsider_get.status_code == 403

    # Outsider user (user3) attempts to send message to conversation -> 403 Forbidden
    outsider_post = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "Sneaking in!"},
        headers=headers3
    )
    assert outsider_post.status_code == 403

def test_public_key_management(client):
    res = client.post("/api/auth/register", json={"username": "cryptouser", "password": "Password123!"})
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Initially public_key is None
    me_res = client.get("/api/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json().get("public_key") is None

    # Update public key
    pubkey = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEtestpublickey12345"
    update_res = client.put("/api/auth/public-key", json={"public_key": pubkey}, headers=headers)
    assert update_res.status_code == 200
    assert update_res.json()["public_key"] == pubkey

    # Check that me endpoint returns the updated key
    me_res2 = client.get("/api/auth/me", headers=headers)
    assert me_res2.json()["public_key"] == pubkey

def test_file_upload_and_media_message(client):
    res = client.post("/api/auth/register", json={"username": "uploader", "password": "Password123!"})
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    res2 = client.post("/api/auth/register", json={"username": "receiver_media", "password": "Password123!"})
    user2_id = res2.json()["user"]["id"]

    # Upload file
    file_content = b"Mock image or document data content"
    files = {"file": ("test_doc.pdf", file_content, "application/pdf")}
    upload_res = client.post("/api/conversations/upload", files=files, headers=headers)
    assert upload_res.status_code == 200
    upload_data = upload_res.json()
    assert "/uploads/" in upload_data["file_url"]
    assert upload_data["file_name"] == "test_doc.pdf"
    assert upload_data["file_size"] == len(file_content)

    # Create conversation and send file message
    conv = client.post("/api/conversations", json={"target_user_id": user2_id}, headers=headers).json()
    conv_id = conv["id"]

    msg_res = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={
            "content": "Check out this document",
            "message_type": "file",
            "file_url": upload_data["file_url"],
            "file_name": upload_data["file_name"],
            "file_size": upload_data["file_size"],
            "is_encrypted": False
        },
        headers=headers
    )
    assert msg_res.status_code == 200
    msg_data = msg_res.json()
    assert msg_data["message_type"] == "file"
    assert msg_data["file_name"] == "test_doc.pdf"
    assert msg_data["file_url"] == upload_data["file_url"]

def test_recent_chats_ordering(client):
    # Register 3 users: Alice, Bob, Charlie
    r_alice = client.post("/api/auth/register", json={"username": "alice", "password": "Password123!"}).json()
    t_alice = r_alice["access_token"]
    h_alice = {"Authorization": f"Bearer {t_alice}"}

    r_bob = client.post("/api/auth/register", json={"username": "bob", "password": "Password123!"}).json()
    bob_id = r_bob["user"]["id"]

    r_charlie = client.post("/api/auth/register", json={"username": "charlie", "password": "Password123!"}).json()
    charlie_id = r_charlie["user"]["id"]

    # Before any chat: users list has bob and charlie without last_message
    users_initial = client.get("/api/auth/users", headers=h_alice).json()
    assert len(users_initial) == 2

    # Alice chats with charlie first
    conv_c = client.post("/api/conversations", json={"target_user_id": charlie_id}, headers=h_alice).json()
    client.post(f"/api/conversations/{conv_c['id']}/messages", json={"content": "Hi Charlie"}, headers=h_alice)

    # Now charlie has a recent chat, bob does not -> Charlie is first!
    users_after_c = client.get("/api/auth/users", headers=h_alice).json()
    assert users_after_c[0]["username"] == "charlie"
    assert users_after_c[0]["last_message"]["content"] == "Hi Charlie"
    assert users_after_c[1]["username"] == "bob"
    assert users_after_c[1]["last_message"] is None

    # Later, Alice chats with bob
    conv_b = client.post("/api/conversations", json={"target_user_id": bob_id}, headers=h_alice).json()
    client.post(f"/api/conversations/{conv_b['id']}/messages", json={"content": "Hi Bob later"}, headers=h_alice)

    # Now Bob's message is newer -> Bob must be at the very top!
    users_after_b = client.get("/api/auth/users", headers=h_alice).json()
    assert users_after_b[0]["username"] == "bob"
    assert users_after_b[0]["last_message"]["content"] == "Hi Bob later"
    assert users_after_b[1]["username"] == "charlie"
