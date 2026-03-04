import json
import os
import time

import redis
import requests
from dotenv import load_dotenv

from app.core.logging_config import setup_logging
from app.utils.token_encriptor import generate_webhook_signature

load_dotenv()
logger = setup_logging()

REDIS_URL = os.getenv("REDIS_URL")
RETRY_DELAYS = [0, 5, 15]  # Быстрые повторы для свежих timestamp


redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)


def send_webhook_with_retries(webhook_url: str, result: dict, task_id: str):
    """ Отправляет результат в вебхук с повторными попытками при неудаче """

    webhook_token = redis_client.get(f"token:{task_id}")

    if not webhook_token:
        logger.error(f"❌ API Token не найден в Redis для task_id: {task_id}")
        return {"status": "failed", "error": "API Token not found"}

    webhook_token = webhook_token.decode("utf-8") if isinstance(webhook_token, bytes) else str(webhook_token)

    payload = json.dumps(result, separators=(',', ':'), sort_keys=True, ensure_ascii=False)

    for attempt, delay in enumerate(RETRY_DELAYS):
        if delay > 0:
            logger.info(f"⏳ Ожидание {delay} секунд перед попыткой #{attempt + 1}")
            time.sleep(delay)

        # Создаём новую временную метку и подпись для каждой попытки
        timestamp = str(int(time.time()))
        signature = generate_webhook_signature(timestamp, payload, webhook_token)

        headers = {
            "X-Signature": signature,
            "X-Request-Timestamp": timestamp,
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(webhook_url, json=result, headers=headers, timeout=30)

            if response.status_code == 200:
                logger.info(f"✅ Вебхук успешно отправлен на {webhook_url}")
                return {"status": "success"}
            else:
                logger.warning(f"⚠️ Ошибка отправки вебхука: {response.status_code} {response.text}")

        except requests.exceptions.RequestException as e:
            logger.error(f"⚠️ Ошибка сети при отправке вебхука: {str(e)}")

    logger.error(f"❌ Достигнут лимит попыток отправки вебхука для task_id: {task_id}")
    return {"status": "failed", "error": "Max retries exceeded"}