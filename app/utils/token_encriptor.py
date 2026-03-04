import hmac
import hashlib


def generate_webhook_signature(timestamp, payload, secret_key):
    """ Генерирует HMAC SHA256 подпись """
    message = f"{timestamp}.{payload}".encode("utf-8")
    signature = hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()

    return signature
