import logging
import os


def setup_logging():
    # Получение уровня логирования из переменной окружения
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Основная конфигурация логирования
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler()  # Логирование в консоль
        ]
    )

    logger = logging.getLogger(__name__)
    return logger
