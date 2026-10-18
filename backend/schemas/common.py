"""Schemas shared by multiple API domains."""

from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    total: int
    page: int
    page_size: int
    items: List[T]
