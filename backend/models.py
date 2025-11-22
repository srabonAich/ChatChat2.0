# Placeholder for MongoDB models using Motor (async)
# You can expand these pydantic models and use Motor to persist messages and file metadata.
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class Message(BaseModel):
    id: Optional[str] = None
    type: str
    frm: str = Field(..., alias='from')
    to: Optional[str]
    room: Optional[str]
    payload: Optional[str]
    ts: datetime = Field(default_factory=datetime.utcnow)

class FileMeta(BaseModel):
    id: Optional[str] = None
    fname: str
    size: int
    sender: str
    key_b64: str
    ts: datetime = Field(default_factory=datetime.utcnow)
