from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import json

from app.core.database import engine, Base, SessionLocal
import app.models  # Import models to register them with Base
from sqlalchemy import inspect, text

# Create tables automatically
Base.metadata.create_all(bind=engine)

def _ensure_columns():
    """Add columns introduced after the first schema version."""
    inspector = inspect(engine)
    dialect = engine.dialect.name
    table_columns = {
        table: {col["name"] for col in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }
    alterations = []
    if "users" in table_columns:
        if "public_key" not in table_columns["users"]:
            alterations.append("ALTER TABLE users ADD COLUMN public_key TEXT")
        if "encrypted_private_key" not in table_columns["users"]:
            alterations.append("ALTER TABLE users ADD COLUMN encrypted_private_key TEXT")
    if "messages" in table_columns:
        cols = table_columns["messages"]
        if "message_type" not in cols:
            alterations.append("ALTER TABLE messages ADD COLUMN message_type VARCHAR(20) DEFAULT 'text' NOT NULL")
        if "file_url" not in cols:
            alterations.append("ALTER TABLE messages ADD COLUMN file_url VARCHAR(500)")
        if "file_name" not in cols:
            alterations.append("ALTER TABLE messages ADD COLUMN file_name VARCHAR(255)")
        if "file_size" not in cols:
            alterations.append("ALTER TABLE messages ADD COLUMN file_size INTEGER")
        if "is_encrypted" not in cols:
            default_false = "0" if dialect == "sqlite" else "0"
            alterations.append(
                f"ALTER TABLE messages ADD COLUMN is_encrypted BOOLEAN DEFAULT {default_false} NOT NULL"
            )
    if alterations:
        with engine.begin() as conn:
            for stmt in alterations:
                try:
                    conn.execute(text(stmt))
                except Exception as e:
                    print(f"Migration notice: {e}")

_ensure_columns()

from fastapi.staticfiles import StaticFiles
import os

from app.api import auth, chat
from app.core.security import decode_access_token
from app.models.user import User
from app.services.connection_manager import manager

app = FastAPI(
    title="ChatConnect API",
    description="Backend API for ChatConnect Real-time Chat Application",
    version="1.0.0",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure uploads directory and mount
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/conversations", tags=["conversations"])

@app.get("/")
def root():
    return {"message": "Welcome to ChatConnect API"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    username = payload.get("sub")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user_id = user.id
    finally:
        db.close()

    await manager.connect(user_id, websocket)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg_data = json.loads(text)
                # Client heartbeat ping/pong or custom websocket events
                if msg_data.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
