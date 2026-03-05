# Клиентский гайд: транскрибация (`/transcribe`)

Ниже инструкция для клиента, у которого уже есть API-токен.

## 1. Базовый URL и авторизация

- Базовый URL: `https://speech.tw1.ru`
- Заголовок авторизации: `Authorization: Bearer <ВАШ_API_TOKEN>`

## 2. Отправка файла в транскрибацию

Метод: `POST /transcribe`  
Тип тела: `multipart/form-data`

Поля формы:

- `file` (обязательно): WAV-файл
- `webhook_url` (необязательно): URL для асинхронного callback с результатом
- `stream_id` (необязательно): ваш ID потока, вернется в webhook
- `is_finished` (необязательно): строка `true` или `false`, по умолчанию `false`

Ограничения:

- Только формат `.wav`
- Максимальный размер файла: `50 MB`
- Максимальная длительность: `15 минут` (900 секунд)

Пример запроса без webhook:

```bash
curl -X POST "https://speech.tw1.ru/transcribe" \
  -H "Authorization: Bearer <ВАШ_API_TOKEN>" \
  -F "file=@/path/to/audio.wav;type=audio/wav"
```

Успешный ответ:

```json
{
  "task_id": "4d5c3f5c-9d7f-4b0a-8df4-9c0f7e9ceabc",
  "status": "processing"
}
```

Пример запроса с webhook:

```bash
curl -X POST "https://speech.tw1.ru/transcribe" \
  -H "Authorization: Bearer <ВАШ_API_TOKEN>" \
  -F "file=@/path/to/audio.wav;type=audio/wav" \
  -F "webhook_url=https://client.example.com/hooks/transcribe" \
  -F "stream_id=dialog-42" \
  -F "is_finished=true"
```

## 2.1 Отправка большого файла (`/transcribe/large`)

Метод: `POST /transcribe/large`  
Тип тела: `multipart/form-data`

Поля формы:

- `file` (обязательно): WAV-файл
- `webhook_url` (необязательно): URL для callback клиенту
- `stream_id` (необязательно): ваш ID потока
- `is_finished` (необязательно): `true` или `false`

Ограничения:

- Только формат `.wav`
- Максимальный размер файла: `1 GB`
- Ограничение по длительности отсутствует
- Если `webhook_url` не передан (или передан пустой строкой), сервис не отправляет callback клиенту:
  результат доступен через polling `GET /transcribe/status/{task_id}`.

Пример:

```bash
curl -X POST "https://speech.tw1.ru/transcribe/large" \
  -H "Authorization: Bearer <ВАШ_API_TOKEN>" \
  -F "file=@/path/to/large_audio.wav;type=audio/wav" \
  -F "webhook_url=https://client.example.com/hooks/transcribe" \
  -F "stream_id=dialog-large-1" \
  -F "is_finished=true"
```

Ответ:

```json
{
  "task_id": "8a8afdb6-4d59-4e96-9cf8-8fb88f8f25a4",
  "status": "processing"
}
```

## 3. Получение результата по `task_id` (polling)

Метод: `GET /transcribe/status/{task_id}`

Пример:

```bash
curl -X GET "https://speech.tw1.ru/transcribe/status/4d5c3f5c-9d7f-4b0a-8df4-9c0f7e9ceabc" \
  -H "Authorization: Bearer <ВАШ_API_TOKEN>"
```

Варианты ответа:

- В обработке:

```json
{
  "task_id": "4d5c3f5c-9d7f-4b0a-8df4-9c0f7e9ceabc",
  "status": "processing"
}
```

- Готово:

```json
{
  "task_id": "4d5c3f5c-9d7f-4b0a-8df4-9c0f7e9ceabc",
  "status": "completed",
  "text": "Текст транскрипции..."
}
```

- Ошибка:

```json
{
  "task_id": "4d5c3f5c-9d7f-4b0a-8df4-9c0f7e9ceabc",
  "status": "failed",
  "error": "Описание ошибки"
}
```

## 4. Формат webhook-ответа (если передан `webhook_url`)

Сервис отправляет `POST` на ваш `webhook_url` с `Content-Type: application/json`.

Тело webhook:

```json
{
  "stream_id": "dialog-42",
  "text": "Текст транскрипции...",
  "type": "transcription",
  "speaker_count": 2,
  "is_finished": true
}
```

Заголовки webhook:

- `X-Signature`: HMAC-SHA256 подпись
- `X-Request-Timestamp`: UNIX timestamp

Подпись считается как HMAC-SHA256 от строки:

`<timestamp>.<canonical_json_payload>`

где `canonical_json_payload` — JSON с сортировкой ключей и без пробелов.

## 5. Ошибки, которые стоит обрабатывать клиенту

- `400 Bad Request`
- Неверный формат файла (не WAV)
- Файл больше 50 MB
- Невалидный WAV
- Длительность больше 15 минут
- `401 Unauthorized` (невалидный/отсутствующий токен)
- `500 Internal Server Error`

Примечание: в текущей конфигурации `401` может возвращаться как редирект на `/login`,
поэтому в API-клиенте лучше отключить auto-follow redirect или отдельно проверять факт редиректа.

## 6. Пример на Python (polling-сценарий)

```python
import time
import requests

BASE_URL = "https://speech.tw1.ru"
API_TOKEN = "<ВАШ_API_TOKEN>"
FILE_PATH = "/path/to/audio.wav"

headers = {"Authorization": f"Bearer {API_TOKEN}"}

with open(FILE_PATH, "rb") as f:
    start_resp = requests.post(
        f"{BASE_URL}/transcribe",
        headers=headers,
        files={"file": ("audio.wav", f, "audio/wav")},
    )
start_resp.raise_for_status()

task_id = start_resp.json()["task_id"]

while True:
    status_resp = requests.get(f"{BASE_URL}/transcribe/status/{task_id}", headers=headers)
    status_resp.raise_for_status()
    data = status_resp.json()

    if data["status"] == "completed":
        print(data["text"])
        break
    if data["status"] == "failed":
        raise RuntimeError(data.get("error", "Unknown error"))

    time.sleep(2)
```
