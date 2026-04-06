"""
app/db/models/attorney.py - Data model for attorneys
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

class FullName(BaseModel):
  courtesy_title: Optional[str] = Field(default=None, description="Courtesy title, e.g. Mr., Ms., Dr.")
  first_name: str = Field(..., description="First name")
  middle_name: Optional[str] = Field(default=None, description="Middle name or initial. Follow initial with periond")
  last_name: str = Field(..., description="Last or family name")
  suffix: Optional[str] = Field(default=None, description="Generational suffix, e.g. Jr., Sr., III, IV")

class BarAdmission(BaseModel):
  bar_number: str = Field(..., description="Bar card number")
  state: str = Field(..., description="State of Admission")

class Attorney(BaseModel):
  name: FullName
  office_id: int = Field(..., description="Index into offices table")
  email: str
  telephone: str
  slug: str = Field(..., description="Second-level domain and alternate attorney identifier. Unique key")
  bar_admissions: List[BarAdmission] = Field(..., description="Bar admissions for this attorneyh")

class AttorneyInDB(Attorney):
  id: int = Field(..., description="Unique key for this table")
  created_at: datetime = Field(..., description="Date/time this record was created. Set by database")
  updated_at: Optional[datetime] = Field(default=None, description="Date/time this record was last updated. Set by database")
  model_config = ConfigDict(from_attributes=True)
