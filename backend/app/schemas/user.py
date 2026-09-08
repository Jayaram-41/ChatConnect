from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str

class PublicKeyUpdate(BaseModel):
    public_key: str
    encrypted_private_key: Optional[str] = None

class LastMessageSnippet(BaseModel):
    id: int
    content: str
    message_type: str = "text"
    file_name: Optional[str] = None
    sender_id: int
    is_encrypted: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UserResponse(UserBase):
    id: int
    public_key: Optional[str] = None
    encrypted_private_key: Optional[str] = None
    created_at: datetime
    last_message: Optional[LastMessageSnippet] = None
    last_message_at: Optional[datetime] = None
    conversation_id: Optional[int] = None
    
    model_config = ConfigDict(from_attributes=True)
