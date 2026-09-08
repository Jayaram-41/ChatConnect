from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional
from app.schemas.user import UserResponse

class MessageBase(BaseModel):
    content: str
    message_type: str = "text"
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    is_encrypted: bool = False

class MessageCreate(BaseModel):
    content: str = ""
    message_type: str = "text"
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    is_encrypted: bool = False

class MessageResponse(MessageBase):
    id: int
    conversation_id: int
    sender_id: int
    created_at: datetime
    sender: Optional[UserResponse] = None
    
    model_config = ConfigDict(from_attributes=True)
