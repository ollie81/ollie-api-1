from pydantic import BaseModel
from typing import Optional

class FCMToken(BaseModel):
    fcm_token: str

class NotificationOut(BaseModel):
    id: str
    title: str
    body: str
    is_read: bool
    created_at: datetime
