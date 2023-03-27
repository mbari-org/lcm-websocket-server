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


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
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
