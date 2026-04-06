"""
logger_factory.py - This module contains the factory class for creating logger instances
"""
import logging
from typing import Optional
from util.settings import settings


class LoggerFactory:
    """Factory class for creating logger instances"""

    @staticmethod
    def create_logger(name: str, loglevel: Optional[str] = None) -> logging.Logger:
        """
        Create a logger instance with the specified name and log level.

        Args:
            name (str): The name of the logger.
            loglevel (str): The log level (e.g., 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL').
                            If not provided, it will default to the value in the environment variable LOGLEVEL.

        Returns:
            logging.Logger: A configured logger instance.
        """
        if loglevel is None:
            loglevel = settings.log_level
            
        loglevel = loglevel.upper()

        # Validate loglevel
        permissible_loglevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if loglevel not in permissible_loglevels:
            # Fallback to INFO if invalid loglevel is provided
            logging.warning(f"Invalid loglevel '{loglevel}' provided. Defaulting to 'INFO'.")
            loglevel = 'INFO'

        result_logger = logging.getLogger(name)
        result_logger.handlers.clear()
        result_logger.setLevel(getattr(logging, loglevel))
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(settings.log_format))
        handler.setLevel(getattr(logging, loglevel))
        result_logger.addHandler(handler)
        result_logger.propagate = False
        result_logger.debug(f"Logger '{name}' created with loglevel '{loglevel}'")
        return result_logger
