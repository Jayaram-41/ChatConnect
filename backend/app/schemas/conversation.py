from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List
from app.schemas.user import UserResponse

class ConversationBase(BaseModel):
    pass

class ConversationCreate(BaseModel):
    target_user_id: int

class ConversationParticipantResponse(BaseModel):
    id: int
    user_id: int
    joined_at: datetime
    user: UserResponse
    
    model_config = ConfigDict(from_attributes=True)

class ConversationResponse(ConversationBase):
    id: int
    created_at: datetime
    participants: List[ConversationParticipantResponse] = []
    
    model_config = ConfigDict(from_attributes=True)
