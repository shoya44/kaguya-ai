from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    turn_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    client_id: UUID
    input_mode: Literal['text', 'voice'] = 'text'
    retry: bool = False

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('empty text')
        return value


class Completion(BaseModel):
    answer: str = Field(min_length=1, max_length=30000)


class Failure(BaseModel):
    status: Literal['failed', 'cancelled']
