from typing import List, Optional

from pydantic import BaseModel, Field


class InvoiceTaxRegistration(BaseModel):
    jurisdiction: str = Field(min_length=2, max_length=100)
    label: str = Field(min_length=2, max_length=50)
    registration_number: str = Field(min_length=2, max_length=100)


class InvoiceDocumentProfile(BaseModel):
    legal_entity_name: Optional[str] = Field(default=None, max_length=150)
    address_lines: List[str] = Field(default_factory=list, max_length=6)
    currency_code: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    currency_locale: str = Field(default="en_IN", pattern=r"^[a-z]{2,3}([_-][A-Z]{2})?$")
    minor_unit_digits: int = Field(default=2, ge=0, le=3)
    currency_symbol: Optional[str] = Field(default=None, max_length=8)
    tax_registrations: List[InvoiceTaxRegistration] = Field(default_factory=list, max_length=10)
    tax_note: Optional[str] = Field(default=None, max_length=500)
    payment_terms: Optional[str] = Field(default=None, max_length=500)
