from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import timedelta
from pydantic import BaseModel

from app.core.database import get_db
from app.models.user import User
from app.models.conversation import ConversationParticipant
from app.models.message import Message
from app.schemas.user import UserCreate, UserResponse, PublicKeyUpdate, LastMessageSnippet
from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

router = APIRouter()

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/register", response_model=Token)
def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = get_password_hash(user.password)
    db_user = User(username=user.username, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "user": db_user}

@router.post("/login", response_model=Token)
def login(login_data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == login_data.username).first()
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "user": user}

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Retrieve the current authenticated user profile."""
    return current_user

@router.put("/public-key", response_model=UserResponse)
def update_public_key(
    key_data: PublicKeyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Store/update ECDH public key and encrypted private key for end-to-end encryption."""
    current_user.public_key = key_data.public_key
    if key_data.encrypted_private_key:
        current_user.encrypted_private_key = key_data.encrypted_private_key
    db.commit()
    db.refresh(current_user)
    return current_user

@router.get("/users", response_model=list[UserResponse])
def get_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retrieve all users in the system with their most recent conversation/message metadata, sorted with recent chats at the top."""
    other_users = db.query(User).filter(User.id != current_user.id).all()
    
    # Get all conversations where current_user participates
    my_part_conv_ids = [
        p[0] for p in db.query(ConversationParticipant.conversation_id)
        .filter(ConversationParticipant.user_id == current_user.id).all()
    ]
    
    user_to_conv_map = {}
    if my_part_conv_ids:
        counterparts = db.query(ConversationParticipant).filter(
            ConversationParticipant.conversation_id.in_(my_part_conv_ids),
            ConversationParticipant.user_id != current_user.id
        ).all()
        for cp in counterparts:
            user_to_conv_map[cp.user_id] = cp.conversation_id

    conv_ids = list(set(user_to_conv_map.values()))
    
    latest_msg_map = {}
    for cid in conv_ids:
        latest_msg = (
            db.query(Message)
            .filter(Message.conversation_id == cid)
            .order_by(Message.created_at.desc())
            .first()
        )
        if latest_msg:
            latest_msg_map[cid] = latest_msg
            
    result = []
    for user in other_users:
        cid = user_to_conv_map.get(user.id)
        last_msg = latest_msg_map.get(cid) if cid else None
        
        last_msg_snippet = None
        last_time = None
        if last_msg:
            last_msg_snippet = LastMessageSnippet(
                id=last_msg.id,
                content=last_msg.content,
                message_type=last_msg.message_type,
                file_name=last_msg.file_name,
                sender_id=last_msg.sender_id,
                is_encrypted=last_msg.is_encrypted,
                created_at=last_msg.created_at
            )
            last_time = last_msg.created_at
            
        u_resp = UserResponse(
            id=user.id,
            username=user.username,
            public_key=user.public_key,
            created_at=user.created_at,
            last_message=last_msg_snippet,
            last_message_at=last_time,
            conversation_id=cid
        )
        result.append(u_resp)
        
    # Sort: users with recent messages first (descending by last_message_at), then users without messages (alphabetical)
    result.sort(
        key=lambda u: (
            0 if u.last_message_at else 1,
            -u.last_message_at.timestamp() if u.last_message_at else 0,
            u.username.lower()
        )
    )
    
    return result
