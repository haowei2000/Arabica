# aiwen/core/logger.py
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from aiwen.config.factory import get_settings

# 获取项目根目录（假设 logger.py 在 aiwen/core/ 下）
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = BASE_DIR / "logs"

# 确保日志目录存在
LOG_DIR.mkdir(exist_ok=True)

def setup_logging():
    settings = get_settings()
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO

    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有处理器（防止重复）
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # === 控制台处理器 ===
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # === 文件处理器（按天轮转，保留30天）===
    file_handler = TimedRotatingFileHandler(
        filename=LOG_DIR / "aiwen.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # （可选）错误日志单独记录
    if not settings.DEBUG:
        error_handler = TimedRotatingFileHandler(
            filename=LOG_DIR / "error.log",
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root_logger.addHandler(error_handler)

    logging.info("Logging is configured. Logs will be written to: %s", LOG_DIR)
