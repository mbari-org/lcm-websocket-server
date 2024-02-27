"""
Logging utilities
"""

import logging

# Common formatter
FORMATTER = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Stream handler
STREAM_HANDLER = logging.StreamHandler()
STREAM_HANDLER.setFormatter(FORMATTER)
STREAM_HANDLER.setLevel(logging.INFO)


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Get a logger. By default, the logger is attached to a StreamHandler with a predefined formatter for consistency.
    
    Args:
        name: Logger name.
        level: Logging level.
    
    Returns:
        Logger.
    """
    logger = logging.Logger(name, level)
    logger.addHandler(STREAM_HANDLER)
    return logger


def set_stream_handler_level(level: int):
    """
    Set the logging level of the stream handler.
    
    Args:
        level: Logging level.
    """
    STREAM_HANDLER.setLevel(level)


def set_stream_handler_verbosity(verbosity: int):
    """
    Set the verbosity of the stream handler.
    
    - 0 = ERROR
    - 1 = WARNING
    - 2 = INFO
    - 3 = DEBUG
    
    Args:
        verbosity: Verbosity level.
    """
    set_stream_handler_level([logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG][max(0, min(verbosity, 3))])

class LogMixin:
    """
    Logging mixin.
    """
    
    @property
    def logger(self) -> logging.Logger:
        """
        Instance logger.
        """
        if not hasattr(self, "_logger"):
            self._logger = get_logger(self.__class__.__name__)
            self._logger.addHandler(logging.NullHandler())
        return self._logger