# API-справка и создание пользователей

## Важно про регистрацию пользователей

Публичная регистрация отключена: эндпоинта `/auth/register` в приложении нет.

Новый пользователь создается только администратором через `POST /admin/users`.

## Как создается первый администратор

1. Перед запуском приложения задайте переменные:
   - `FIRST_SUPERUSER_EMAIL`
   - `FIRST_SUPERUSER_PASSWORD`
2. На старте (`app/main.py`) вызывается `create_first_superuser()`.
3. Если пользователь с таким email уже есть, он не пересоздается и пароль не перезаписывается.

## Пошагово: создать нового пользователя через админа

1. Войти под админом и сохранить cookie:

```bash
curl -i -c cookies.txt -X POST "https://<YOUR_DOMAIN>/auth/jwt/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@example.com&password=<ADMIN_PASSWORD>"
```

Ожидаемо: `204 No Content` и cookie `auth_token`.

2. Создать пользователя (обычного):

```bash
curl -X POST "https://<YOUR_DOMAIN>/admin/users" \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{
    "email": "new.user@example.com",
    "password": "StrongPassword123",
    "is_superuser": false
  }'
```

3. Для создания второго администратора передайте `"is_superuser": true`.

## Авторизация в API

Используются 2 схемы авторизации:

- Cookie-сессия (`auth_token`) после `POST /auth/jwt/login`:
  - для веб-страниц и пользовательских/админских endpoint'ов.
- `Authorization: Bearer <API_TOKEN>`:
  - для `/transcribe` и `/transcribe/status/{task_id}`.
  - токен генерируется через `POST /generate_api_token/`.

## Полный список эндпоинтов

### Системные

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/` | Публичный | Редирект на `/dashboard`. |
| GET | `/metrics` | Публичный | Метрики Prometheus. |

### Страницы

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/login` | Публичный | HTML-страница логина. |
| GET | `/dashboard` | Пользователь (`auth_token`) | Пользовательский дашборд. |
| GET | `/admin/dashboard` | Админ (`auth_token`) | Админ-дашборд. |
| GET | `/api_token` | Пользователь (`auth_token`) | Страница управления API-токеном. |
| GET | `/webhook_token` | Пользователь (`auth_token`) | Страница управления webhook-токеном. |

### Аутентификация и пользователи

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/auth/jwt/login` | Публичный | Логин по `application/x-www-form-urlencoded` (`username`, `password`), ставит cookie `auth_token`. |
| POST | `/auth/jwt/logout` | Пользователь (`auth_token`) | Логаут, очищает cookie. |
| GET | `/users/me` | Пользователь (`auth_token`) | Профиль текущего пользователя. |
| PATCH | `/users/me` | Пользователь (`auth_token`) | Частичное обновление своего профиля. |
| GET | `/users/{id}` | Админ (`auth_token`) | Получить пользователя по ID. |
| PATCH | `/users/{id}` | Админ (`auth_token`) | Обновить пользователя по ID. |
| DELETE | `/users/{id}` | Админ (`auth_token`) | Удалить пользователя по ID. |
| POST | `/admin/users` | Админ (`auth_token`) | Создать нового пользователя. Тело: `email`, `password`, `is_superuser`. |

### Токены

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/generate_api_token/` | Пользователь (`auth_token`) | Создать/обновить API-токен пользователя. |
| DELETE | `/delete_api_token/` | Пользователь (`auth_token`) | Удалить свой API-токен. |
| POST | `/generate_webhook_token/` | Пользователь (`auth_token`) | Создать/обновить webhook-токен пользователя. |
| GET | `/get_webhook_token/` | Пользователь (`auth_token`) | Получить текущий webhook-токен. |
| DELETE | `/delete_webhook_token/` | Пользователь (`auth_token`) | Удалить свой webhook-токен. |

Токены также можно управлять прямо из личного кабинета (`/dashboard`) в блоке "API токен / Webhook токен":
- генерация/ротация;
- удаление;
- просмотр и копирование webhook-токена.

### Транскрибация

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/transcribe` | API token (`Bearer`) | Отправить WAV-файл на транскрибацию (асинхронно). |
| POST | `/transcribe/large` | API token (`Bearer`) | Отправить большой аудио/видео-файл в ElevenLabs async webhook flow. |
| GET | `/transcribe/status/{task_id}` | API token (`Bearer`) | Проверить статус и результат задачи. |
| POST | `/webhooks/elevenlabs` | Relay webhook | Внутренний callback endpoint для результата от ElevenLabs (через relay). |

Для `POST /transcribe`:
- `multipart/form-data`, поле `file` обязательно.
- Дополнительно: `webhook_url`, `stream_id`, `is_finished`.
- Ограничения: только `.wav`, до `50 MB`, до `15 минут`.

Для `POST /transcribe/large`:
- `multipart/form-data`, нужно передать **ровно один** источник:
  - либо поле `file`,
  - либо поле `cloud_storage_url`.
- Дополнительно: `webhook_url`, `stream_id`, `is_finished`.
- Ограничения для `file`: до `1 GB`, ограничение по длительности отсутствует.
- Если размер `file` больше `20 MB`, endpoint возвращает ошибку с требованием использовать `cloud_storage_url`.
- Убедитесь, что reverse-proxy принимает тела такого размера (в Caddy: `request_body.max_size`).
- Для `file`: файл принимается потоково (чанками), сохраняется временно на диск и загружается в S3.
- Для `cloud_storage_url`: ссылка передается в ElevenLabs как `cloud_storage_url`.
- Endpoint возвращает `task_id`, а финальный статус/результат проверяется через `/transcribe/status/{task_id}`.

### Настройка ElevenLabs callback для `/transcribe/large`

1. В ElevenLabs создайте Speech-to-Text webhook URL:
   - `https://<YOUR_DOMAIN>/webhooks/elevenlabs`
2. Если webhook в ElevenLabs несколько, укажите нужный ID в env:
   - `ELEVENLABS_WEBHOOK_ID=<id>`
3. Endpoint `/transcribe/large` передает в ElevenLabs `webhook_metadata` с `task_id`,
   поэтому callback автоматически связывается с исходной задачей.
4. После получения callback сервис пересылает результат вашему `webhook_url`
   в том же клиентском формате:
   - `stream_id`, `text`, `type`, `speaker_count`, `is_finished`.

### Защита входящего relay webhook (`/webhooks/elevenlabs`)

Перед бизнес-обработкой сервис проверяет HMAC-подпись по `raw body`:

- Заголовок подписи: `DOWNSTREAM_HMAC_HEADER` (по умолчанию `x-relay-signature`)
- Заголовок timestamp: `DOWNSTREAM_TIMESTAMP_HEADER` (по умолчанию `x-relay-timestamp`)
- Алгоритм: `HMAC_SHA256(DOWNSTREAM_HMAC_SECRET, f"{timestamp}." + raw_body)`
- Сравнение: `hmac.compare_digest`
- Окно timestamp: `RELAY_TIMESTAMP_TOLERANCE_SECONDS` (по умолчанию `300`)

При любой ошибке верификации endpoint возвращает:
- `401 {"detail":"invalid relay signature"}`

### Биллинг

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/audio_usage/` | Пользователь (`auth_token`) | Статистика минут за период (`start_date`, `end_date`). |
| GET | `/admin/users/` | Админ (`auth_token`) | Список пользователей (без суперпользователей). |
| POST | `/admin/audio_usage/` | Админ (`auth_token`) | Статистика по выбранным пользователям или по всем. |

## Частые причины 401

- Нет cookie `auth_token` для защищенных endpoint'ов.
- Нет заголовка `Authorization: Bearer <token>` для endpoint'ов транскрибации.
- Невалидный токен.

Примечание: в приложении есть обработчик, который может редиректить `401` на `/login`.
Для API-клиентов лучше явно контролировать редиректы.
