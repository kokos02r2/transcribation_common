import datetime
import hashlib
import hmac
import mimetypes
import os

import boto3
import requests
from botocore.client import Config
from dotenv import load_dotenv

from app.core.logging_config import setup_logging

# Загружаем переменные окружения
load_dotenv()
logger = setup_logging()

S3_ENDPOINT = "https://s3.timeweb.cloud"
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_REGION = "ru-1"  # Регион Timeweb


def _get_s3_client():
    if not S3_ACCESS_KEY or not S3_SECRET_KEY or not S3_BUCKET_NAME:
        raise RuntimeError("S3 credentials are not configured")
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4"),
    )


def build_s3_object_url(file_name: str) -> str:
    normalized_key = (file_name or "").lstrip("/")
    if not normalized_key:
        raise RuntimeError("S3 object key is empty")
    return f"{S3_ENDPOINT}/{S3_BUCKET_NAME}/{normalized_key}"


def generate_presigned_download_url(file_name: str, expires_seconds: int) -> str:
    normalized_key = (file_name or "").lstrip("/")
    if not normalized_key:
        raise RuntimeError("S3 object key is empty")

    s3_client = _get_s3_client()
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET_NAME, "Key": normalized_key},
        ExpiresIn=max(int(expires_seconds), 60),
    )


def sign(key, msg):
    """Создает подпись HMAC-SHA256."""
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def get_signature_key(secret_key, date_stamp, region, service):
    """Генерирует ключ подписи AWS v4."""
    k_date = sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, "aws4_request")
    return k_signing


def upload_to_s3(file_path: str, file_name: str) -> str:
    """Загружает файл в S3 и возвращает публичный URL."""
    try:
        logger.info(f"Начинаем загрузку файла {file_name} в S3...")

        now = datetime.datetime.utcnow()
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        canonical_uri = f"/{S3_BUCKET_NAME}/{file_name}"
        canonical_headers = (
            f"host:s3.timeweb.cloud\n"
            f"x-amz-content-sha256:UNSIGNED-PAYLOAD\n"
            f"x-amz-date:{timestamp}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        payload_hash = "UNSIGNED-PAYLOAD"

        canonical_request = (
            f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

        scope = f"{datestamp}/{S3_REGION}/s3/aws4_request"
        string_to_sign = f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{hashed_canonical_request}"

        signing_key = get_signature_key(S3_SECRET_KEY, datestamp, S3_REGION, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = (
            f"AWS4-HMAC-SHA256 Credential={S3_ACCESS_KEY}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        content_type = (
            mimetypes.guess_type(file_name)[0]
            or mimetypes.guess_type(file_path)[0]
            or "application/octet-stream"
        )

        headers = {
            "Authorization": authorization_header,
            "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
            "x-amz-date": timestamp,
            "Content-Type": content_type,
        }

        with open(file_path, "rb") as f:
            response = requests.put(f"{S3_ENDPOINT}/{S3_BUCKET_NAME}/{file_name}", headers=headers, data=f)

        if response.status_code == 200:
            s3_url = f"{S3_ENDPOINT}/{S3_BUCKET_NAME}/{file_name}"
            logger.info(f"Файл {file_name} успешно загружен в S3: {s3_url}")
            return s3_url
        else:
            logger.error(f"Ошибка загрузки файла {file_name} в S3: {response.status_code} {response.text}")
            raise RuntimeError(f"Ошибка загрузки: {response.status_code} {response.text}")

    except Exception as e:
        logger.error(f"Ошибка при загрузке файла {file_name}: {str(e)}")
        raise RuntimeError(f"Ошибка при загрузке файла {file_name}: {str(e)}")


def delete_from_s3(file_name: str):
    """Удаляет файл из S3."""
    try:
        logger.info(f"Начинаем удаление файла {file_name} из S3...")

        now = datetime.datetime.utcnow()
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        canonical_uri = f"/{S3_BUCKET_NAME}/{file_name}"
        canonical_headers = (
            f"host:s3.timeweb.cloud\n"
            f"x-amz-content-sha256:UNSIGNED-PAYLOAD\n"
            f"x-amz-date:{timestamp}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        payload_hash = "UNSIGNED-PAYLOAD"

        canonical_request = (
            f"DELETE\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

        scope = f"{datestamp}/{S3_REGION}/s3/aws4_request"
        string_to_sign = f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{hashed_canonical_request}"

        signing_key = get_signature_key(S3_SECRET_KEY, datestamp, S3_REGION, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = (
            f"AWS4-HMAC-SHA256 Credential={S3_ACCESS_KEY}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Authorization": authorization_header,
            "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
            "x-amz-date": timestamp,
        }

        response = requests.delete(f"{S3_ENDPOINT}/{S3_BUCKET_NAME}/{file_name}", headers=headers)

        if response.status_code == 204:
            logger.info(f"Файл {file_name} успешно удален из S3.")
        else:
            logger.error(f"Ошибка удаления файла {file_name} из S3: {response.status_code} {response.text}")
            raise RuntimeError(f"Ошибка удаления: {response.status_code} {response.text}")

    except Exception as e:
        logger.error(f"Ошибка при удалении файла {file_name}: {str(e)}")
