import mimetypes
import os

import boto3
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
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
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


def _normalize_object_key(file_name: str) -> str:
    normalized_key = (file_name or "").lstrip("/")
    if not normalized_key:
        raise RuntimeError("S3 object key is empty")
    return normalized_key


def upload_to_s3(file_path: str, file_name: str) -> str:
    """Загружает файл в S3 и возвращает публичный URL."""
    try:
        normalized_key = _normalize_object_key(file_name)
        content_type = (
            mimetypes.guess_type(normalized_key)[0]
            or mimetypes.guess_type(file_path)[0]
            or "application/octet-stream"
        )

        logger.info(f"Начинаем загрузку файла {normalized_key} в S3...")
        s3_client = _get_s3_client()
        s3_client.upload_file(
            Filename=file_path,
            Bucket=S3_BUCKET_NAME,
            Key=normalized_key,
            ExtraArgs={"ContentType": content_type},
        )

        s3_url = build_s3_object_url(normalized_key)
        logger.info(f"Файл {normalized_key} успешно загружен в S3: {s3_url}")
        return s3_url

    except Exception as e:
        logger.error(f"Ошибка при загрузке файла {file_name}: {str(e)}")
        raise RuntimeError(f"Ошибка при загрузке файла {file_name}: {str(e)}")


def delete_from_s3(file_name: str):
    """Удаляет файл из S3."""
    try:
        normalized_key = _normalize_object_key(file_name)
        logger.info(f"Начинаем удаление файла {normalized_key} из S3...")
        s3_client = _get_s3_client()
        s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=normalized_key)
        logger.info(f"Файл {normalized_key} успешно удален из S3.")

    except Exception as e:
        logger.error(f"Ошибка при удалении файла {file_name}: {str(e)}")
        raise RuntimeError(f"Ошибка при удалении файла {file_name}: {str(e)}")
