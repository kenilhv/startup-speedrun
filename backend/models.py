from enum import Enum
from pydantic import BaseModel, Field

class CallOutcome(str, Enum):
    confirmed = "confirmed"
    denied = "denied"
    no_answer = "no_answer"
    unclear = "unclear"

class CallResult(BaseModel):
    invoice_id: int = Field(gt=0)
    provider_call_id: str | None = Field(default=None, max_length=256)
    outcome: CallOutcome
    summary: str = Field(default="", max_length=4000)
    transcript: str | None = Field(default=None, max_length=50000)
