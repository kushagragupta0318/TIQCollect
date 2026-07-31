from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


class CaseFilterParams(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    agent_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    limit: int = 50
    offset: int = 0
