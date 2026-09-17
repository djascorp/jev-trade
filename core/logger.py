import sys
import os
from loguru import logger

logger.remove()

_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

logger.add(
    sys.stderr,
    format=(
        "<level>{level: <8}</level> | "
        "<cyan>{time:YYYY-MM-DD HH:mm:ss}</cyan> | "
        "<level>{message}</level>"
    ),
    level=_log_level,
    colorize=True,
)

if _log_level != "CRITICAL":
    logger.add(
        "bloc-trade.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        enqueue=True,  # Thread-safe logging to avoid file lock issues
        delay=True,  # Delay file creation until first write
    )


def get_logger(name: str = "bloc-trade"):
    return logger.bind(name=name)
