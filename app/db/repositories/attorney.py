"""
app/db/repositories/attorney.py - Repository for Attorney model
"""
from app.db.repositories.base_repo import BaseRepository
from app.db.models.attorney import AttorneyInDB
from app.db.supabasemanager import DatabaseManager

class AttorneyRepository(BaseRepository[AttorneyInDB]):
    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "attorneys", AttorneyInDB)