from abc import ABC, abstractmethod
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, Type, TypeVar
from postgrest import SyncSelectRequestBuilder
from pydantic import BaseModel
from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from postgrest.base_request_builder import APIResponse
from postgrest.types import CountMethod
from postgrest.exceptions import APIError

from util.settings import Settings
from util.loggerfactory import LoggerFactory

import logging

# Set the underlying libraries to DEBUG
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("postgrest").setLevel(logging.DEBUG)

LOGGER = LoggerFactory.create_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def _json_safe(data: dict[str, Any]) -> dict[str, Any]:
    """Convert datetime and enum values in a dict so httpx can JSON-serialize it."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, Enum):
            out[k] = v.value
        elif isinstance(v, dict):
            out[k] = _json_safe(v)
        else:
            out[k] = v
    return out

SETTINGS = Settings()

class DatabaseManager(ABC):
    @abstractmethod
    def select_one(self, table:str, result_type: Type[T], condition: dict[str, Any], select_string: str = "*") -> Optional[T]:
        pass

    @abstractmethod
    def select_many(self, table:str, result_type: Type[T], condition: dict[str, Any], select_string: str = "*", sort_by: Optional[str] = None, sort_direction: str = "asc", start: Optional[int] = None, end: Optional[int] = None) -> tuple[list[T], int]:
        pass

    @abstractmethod
    def insert(self, table:str, data: dict[str, Any], result_type: Type[T]) -> T:
        pass

    @abstractmethod
    def update(self, table:str, record_id: Any, data: dict[str, Any], result_type: Type[T]) -> T:
        pass

    @abstractmethod
    def delete(self, table:str, record_id: Any) -> bool:
        pass

    @abstractmethod
    def exists(self, table:str, field: str, value: Any) -> bool:
        pass

class SupabaseManager(DatabaseManager):
    def __init__(self):
        self.url = SETTINGS.supabase_url
        self.key = SETTINGS.supabase_service_role_key
        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
        self.client: Client = create_client(self.url, self.key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(APIError)
    )
    def select_one(
        self,
        table:str,
        result_type: Type[T],
        condition: dict[str, Any],
        select_string: str = "*"
        ) -> Optional[T]:

        query: SyncSelectRequestBuilder = self.client.table(table).select(select_string)
        for field, value in condition.items():
            if value is None:
                query = query.is_(field, None)
            else:
                query = query.eq(field, value)

        try:
            result: APIResponse = query.single().execute()
        except APIError as e:
            if e.code == 'PGRST116':  # No match found
                return None
            LOGGER.error("Error executing select_one query on table %s with condition %s: %s", table, condition, str(e))
            raise

        if isinstance(result.data, dict):
            return result_type(**result.data)
        raise ValueError(f"No record found matching the condition: %s", result.data)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(APIError)
    )
    def select_many(
        self,
        table:str,
        result_type: Type[T],
        condition: dict[str, Any],
        select_string: str = "*",
        sort_by: Optional[str] = None,
        sort_direction: str = "asc",
        start: Optional[int] = None,
        end: Optional[int] = None
        ) -> tuple[list[T], int]:
        """
        Select multiple records from the specified table based on conditions.
        
        Args:
        :param table: Name of the target table to query.
        :type table: str

        :param result_type: Subclass of BaseModel to parse the results into.
        :type result_type: Type[BaseModel]

        :param condition: Dictionary of field-value pairs for filtering results.
        :type condition: dict[str, Any]

        :param select_string: String specifying which columns to select.
        :type select_string: str

        :param sort_by: Field to sort the results by.
        :type sort_by: Optional[str]

        :param sort_direction: Direction to sort the results ("asc" or "desc").
        :type sort_direction: str

        :param start: Starting index for the range of results to fetch.
        :type start: Optional[int]

        :param end: Ending index for the range of results to fetch.
        :type end: Optional[int]

        :return: A tuple containing a list of parsed BaseModel instances and the total count of matching records.
        :rtype: tuple[list[BaseModel], int]
        """
        query = self.client.table(table).select(select_string, count=CountMethod.exact)

        for field, value in condition.items():
            if value is None:
                query = query.is_(field, None)
            else:
                query = query.eq(field, value)
        if sort_by:
            query = query.order(sort_by, desc=(sort_direction.lstrip().lower() == "desc"))
        if start is not None and end is not None:
            query = query.range(start, end)
    
        result = query.execute()
        if not result.data:
            return [], 0
        return [result_type(**item) for item in result.data], result.count or 0  # type: ignore

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(APIError)
    )
    def insert(self, table: str, data: Any, result_type: Type[T]) -> T:
        """
        Docstring for insert
        
        Args:
        :param table: Name of the target table to insert data into.
        :type table: str
        :param data: Dictionary containing the data to be inserted.
        :type data: dict[str, Any]
        :param result_type: Subclass of BaseModel to parse the inserted record into.
        :type result_type: Type[BaseModel]

        :return: Parsed instance of the inserted record.
        :rtype: BaseModel
        """
        if isinstance(data, str):
            LOGGER.error("CRITICAL: String passed to insert instead of dict. Raising error.")
            raise ValueError("The 'data' argument must be a dictionary, not a JSON string.")

        data = _json_safe(data)
        try:
            result = self.client.table(table).insert(data).execute()
            if not result.data:
                LOGGER.error("Result of insert is empty: %s", result)
                raise ValueError(f"Insert operation failed for table {table} with data: {data}")
            return result_type(**result.data[0])  # type: ignore
        except Exception as e:
            LOGGER.error(f"Error inserting into {table}: {e}")
            LOGGER.exception(e)
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(APIError)
    )
    def update(self, table:str, record_id: Any, data: dict[str, Any], result_type: Type[T]) -> T:
        """
        Docstring for update
        
        Args:
        :param table: Name of the target table to update data in.
        :type table: str
        :param record_id: Identifier of the record to be updated.
        :type record_id: Any
        :param data: Dictionary containing the data to be updated.
        :type data: dict[str, Any]
        :param result_type: Subclass of BaseModel to parse the updated record into.
        :type result_type: Type[BaseModel]

        :return: Parsed instance of the updated record.
        :rtype: BaseModel
        """
        data = _json_safe(data)
        try:
            result = self.client.table(table).update(data).eq("id", record_id).execute()
            return result_type(**result.data[0])  # type: ignore
        except Exception as e:
            LOGGER.error("Error updating record in %s with ID %s: %s", table, record_id, e)
            LOGGER.exception(e)
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(APIError)
    )
    def delete(self, table:str, record_id: Any) -> bool:
        """
        Docstring for delete
        
        :param self: Description
        :param table: Description
        :type table: str
        :param record_id: Description
        :type record_id: Any

        :return: Description
        :rtype: bool
        """
        _ = self.client.table(table).delete().eq("id", record_id).execute()
        return True
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(APIError)
    )
    def exists(self, table:str, field: str, value: Any) -> bool:
        """
        Docstring for exists
        
        Args:
        :param self: Description
        :param table: Description
        :type table: str
        :param field: Description
        :type field: str
        :param value: Description
        :type value: Any

        :return: Description
        :rtype: bool
        """
        try:
            result = self.client.table(table).select("id", count=CountMethod.exact).eq(field, value).single().execute()
            return (result.count or 0) > 0
        except APIError as e:
            if e.code == 'PGRST116':  # No match found
                return False
        except Exception as e:
            LOGGER.error("exists(): %s", e)
            if logging.getLevelName(LOGGER.getEffectiveLevel()) == "DEBUG":
                LOGGER.exception(e)
            raise
        return False
