"""
app/schemas/category_rule.py - Keyword rules that file transactions.
"""
from typing import Optional

from pydantic import BaseModel, Field


class CategoryRuleWriteRequest(BaseModel):
    """Create or change a rule."""
    pattern: str = Field(
        ...,
        min_length=3,
        max_length=120,
        description="Matched case- and punctuation-insensitively against the description and "
                    "the counterparty, so WALMART finds 'WAL-MART #1234' and 'WALMART.COM'. "
                    "Three characters minimum — a shorter one matches half the production",
    )
    category_id: int = Field(..., description="Where a matching line is filed")
    matter_id: Optional[int] = Field(
        default=None,
        description="Omit for a firm-wide rule. A matter id scopes it to that case — for the "
                    "client whose EXXON lines are revenue rather than fuel",
    )
    priority: int = Field(
        default=100,
        description="Lower fires first. WALMART PHARMACY must beat WALMART, or medical "
                    "spending lands in household supplies",
    )
    applies_to: str = Field(
        default="any",
        pattern="^(any|credit|debit)$",
        description="PAYROLL arriving is income; PAYROLL leaving is a business expense",
    )
    is_active: bool = Field(default=True)
    note: Optional[str] = Field(
        default=None,
        max_length=300,
        description="Why the rule exists, for whoever inherits it",
    )


class CategoryRuleResponse(BaseModel):
    id: int
    matter_id: Optional[int]
    pattern: str
    category_id: int
    priority: int
    applies_to: str
    is_active: bool
    note: Optional[str]
    is_firm_wide: bool = Field(
        ...,
        description="True when the rule governs every case. An editor that could not tell "
                    "would offer to change it from inside one matter",
    )
    transactions_filed: int = Field(
        default=0,
        description="How many lines this rule filed. The number that says whether deleting it "
                    "is tidying up or unpicking three hundred assignments",
    )


class RuleRunResult(BaseModel):
    """What a re-run did."""
    rules: int = Field(..., description="Rules in force on the matter")
    examined: int = Field(..., description="Lines eligible to be filed — a person's are not")
    filed: int = Field(..., description="Had no category and now do")
    refiled: int = Field(..., description="A machine had filed them differently")
    unmatched: int = Field(
        ...,
        description="No rule claimed them. This is the paralegal's remaining work, and the "
                    "pool a curated similarity library would later draw from",
    )
