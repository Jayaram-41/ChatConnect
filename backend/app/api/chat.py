import os
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.conversation import Conversation, ConversationParticipant
from app.models.message import Message
from app.schemas.conversation import ConversationCreate, ConversationResponse
from app.schemas.message import MessageCreate, MessageResponse
from app.core.security import get_current_user
from app.services.connection_manager import manager

router = APIRouter()

UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "uploads"
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Upload a file or photo attachment (max 25MB)."""
    file_ext = os.path.splitext(file.filename)[1] if file.filename else ""
    unique_filename = f"{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    file_size = 0
    try:
        with open(file_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > 25 * 1024 * 1024:
                    buffer.close()
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="File too large (max 25MB)"
                    )
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {str(e)}"
        )
        
    return {
        "file_url": f"/uploads/{unique_filename}",
        "file_name": file.filename or unique_filename,
        "file_size": file_size,
        "content_type": file.content_type
    }

@router.post("", response_model=ConversationResponse)
def get_or_create_conversation(
    conv_data: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retrieve existing 1-on-1 conversation or create a new one with target user."""
    if conv_data.target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create conversation with yourself"
        )
    
    target_user = db.query(User).filter(User.id == conv_data.target_user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found"
        )
        
    # Check if a 1-on-1 conversation already exists between these two users
    p1 = db.query(ConversationParticipant.conversation_id).filter(
        ConversationParticipant.user_id == current_user.id
    ).scalar_subquery()
    
    existing = db.query(ConversationParticipant.conversation_id).filter(
        ConversationParticipant.user_id == conv_data.target_user_id,
        ConversationParticipant.conversation_id.in_(p1)
    ).first()
    
    if existing:
        conv = db.query(Conversation).filter(Conversation.id == existing[0]).first()
        return conv
        
    # Create new conversation
    new_conv = Conversation()
    db.add(new_conv)
    db.flush()
    
    p_current = ConversationParticipant(conversation_id=new_conv.id, user_id=current_user.id)
    p_target = ConversationParticipant(conversation_id=new_conv.id, user_id=conv_data.target_user_id)
    db.add(p_current)
    db.add(p_target)
    
    db.commit()
    db.refresh(new_conv)
    return new_conv

@router.get("", response_model=List[ConversationResponse])
def get_my_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retrieve all conversations for the authenticated user."""
    user_conv_ids = db.query(ConversationParticipant.conversation_id).filter(
        ConversationParticipant.user_id == current_user.id
    ).scalar_subquery()
    
    latest_sub = (
        db.query(
            Message.conversation_id.label("conversation_id"),
            func.max(Message.created_at).label("last_at")
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    conversations = (
        db.query(Conversation)
        .filter(Conversation.id.in_(user_conv_ids))
        .outerjoin(latest_sub, Conversation.id == latest_sub.c.conversation_id)
        .order_by(desc(latest_sub.c.last_at), Conversation.created_at.desc())
        .all()
    )

    return conversations

@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
def get_conversation_messages(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retrieve message history for a conversation."""
    participant = db.query(ConversationParticipant).filter(
        ConversationParticipant.conversation_id == conversation_id,
        ConversationParticipant.user_id == current_user.id
    ).first()
    
    if not participant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant in this conversation"
        )
        
    messages = db.query(Message).filter(
        Message.conversation_id == conversation_id
    ).order_by(Message.created_at.asc()).all()
    
    return messages

@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(
    conversation_id: int,
    msg_data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Send a message (text, photo, or file) to a conversation and broadcast to active participants."""
    if not msg_data.content.strip() and not msg_data.file_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message must contain content or an attachment"
        )
        
    participant = db.query(ConversationParticipant).filter(
        ConversationParticipant.conversation_id == conversation_id,
        ConversationParticipant.user_id == current_user.id
    ).first()
    
    if not participant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant in this conversation"
        )
        
    new_message = Message(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        content=msg_data.content.strip() if msg_data.content else "",
        message_type=msg_data.message_type or "text",
        file_url=msg_data.file_url,
        file_name=msg_data.file_name,
        file_size=msg_data.file_size,
        is_encrypted=msg_data.is_encrypted,
    )
    db.add(new_message)
    db.commit()
    db.refresh(new_message)
    
    # Broadcast via WebSocket to all participants in this conversation
    participants = db.query(ConversationParticipant.user_id).filter(
        ConversationParticipant.conversation_id == conversation_id
    ).all()
    participant_ids = [p[0] for p in participants]
    
    socket_payload = {
        "type": "new_message",
        "message": {
            "id": new_message.id,
            "conversation_id": new_message.conversation_id,
            "sender_id": new_message.sender_id,
            "content": new_message.content,
            "message_type": new_message.message_type,
            "file_url": new_message.file_url,
            "file_name": new_message.file_name,
            "file_size": new_message.file_size,
            "is_encrypted": new_message.is_encrypted,
            "created_at": new_message.created_at.isoformat() if new_message.created_at else None,
            "sender": {
                "id": current_user.id,
                "username": current_user.username,
                "public_key": current_user.public_key,
                "created_at": current_user.created_at.isoformat() if current_user.created_at else None
            }
        }
    }
    await manager.broadcast_to_users(participant_ids, socket_payload)
    
    return new_message
