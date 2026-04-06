from typing import TypeVar, Generic, List, Type, Any, Optional
from pydantic import BaseModel
from db.supabasemanager import DatabaseManager

T = TypeVar("T", bound=BaseModel)

class BaseRepository(Generic[T]):
    def __init__(self, manager: DatabaseManager, table_name: str, model_class: Type[T]):
        self.manager = manager
        self.table_name = table_name
        self.model_class = model_class

    def select_one(self, condition: dict[str, Any], select_string: str = "*") -> Optional[T]:
        return self.manager.select_one(self.table_name, self.model_class, condition, select_string)
    
    def select_many(self, condition: dict[str, Any], select_string: str = "*", sort_by: Optional[str] = None, sort_direction: str = "asc", start: Optional[int] = None, end: Optional[int] = None) -> tuple[List[T], int]:
        return self.manager.select_many(self.table_name, self.model_class, condition, select_string, sort_by, sort_direction, start, end)
    
    def insert(self, data: dict[str, Any]) -> T:
        return self.manager.insert(self.table_name, data, self.model_class)
    
    def update(self, record_id: Any, data: dict[str, Any]) -> T:
        return self.manager.update(self.table_name, record_id, data, self.model_class)
    
    def delete(self, record_id: Any) -> bool:
        return self.manager.delete(self.table_name, record_id)
    
    def exists(self, field: str, value: Any) -> bool:
        return self.manager.exists(self.table_name, field, value)
  
