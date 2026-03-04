# Сервис транскрибации речи

Веб-сервис для обработки и транскрибации аудио, построенный на FastAPI и PyTorch. Сервис анализирует аудиофайлы и предоставляет детальную статистику использования.

## Возможности

- Обработка аудиофайлов и определение речи
- Сегментация речи и не-речи
- Статистика использования и биллинг
- Аутентификация и авторизация пользователей
- API-эндпоинты для обработки аудио
- Веб-интерфейс с дашбордом
- Фоновая обработка задач с помощью Celery
- Интеграция с Prometheus для мониторинга

## Технологии

- **Веб-фреймворк**: FastAPI
- **База данных**: PostgreSQL с SQLAlchemy и Alembic
- **Очередь задач**: Celery с Redis
- **ML-фреймворк**: PyTorch с Silero VAD
- **Хранилище**: AWS S3
- **Мониторинг**: Prometheus
- **Аутентификация**: FastAPI Users

## Требования

- Python 3.10 или выше
- PostgreSQL
- Redis
- Poetry (для управления зависимостями)

## Установка

1. Клонируйте репозиторий:

```bash
git clone <repository-url>
cd speech_diarization
```

2. Установите зависимости с помощью Poetry:

```bash
poetry install
```

Для установки только CPU версии:

```bash
poetry install --with pytorch-cpu
```

3. Настройте переменные окружения:

```bash
cp .env.example .env
# Отредактируйте .env файл под свою конфигурацию
```

4. Выполните миграции базы данных:

```bash
alembic upgrade head
```

## Конфигурация

Необходимо настроить следующие переменные окружения:

- `DATABASE_URL`: Строка подключения к PostgreSQL
- `REDIS_URL`: Строка подключения к Redis
- `AWS_ACCESS_KEY_ID`: Ключ доступа AWS
- `AWS_SECRET_ACCESS_KEY`: Секретный ключ AWS
- `S3_BUCKET`: Имя S3 бакета
- `SECRET`: Секретный ключ приложения
- `COOKIE_SECURE`: Отправлять auth-cookie только по HTTPS (`true` для продакшна)
- `ALLOW_HTTP_WEBHOOKS`: Разрешать ли `http://` webhook URL (по умолчанию `false`)
- `ALLOW_PRIVATE_WEBHOOK_HOSTS`: Разрешать ли webhook на private/local адреса (по умолчанию `false`)
- `ELEVENLABS_API_KEY`: API-ключ ElevenLabs
- `ELEVENLABS_PROXY_URL`: SOCKS5 прокси для ElevenLabs и Gemini (например, `socks5://user:pass@host:port`)
- `GEMINI_API_KEY`: API-ключ Gemini
- `GEMINI_MODEL_NAME`: Модель Gemini (по умолчанию `gemini-3-flash-preview`)
- `GEMINI_PROMPT`: Промпт для транскрибации (по умолчанию встроенный)
- `GEMINI_RESPONSE_JSON`: Запрашивать JSON-ответ от Gemini и брать только поле `text` (по умолчанию `true`)
- `GEMINI_TEMPERATURE`: Температура генерации Gemini (по умолчанию `0`)
- `GEMINI_TOP_P`: Параметр top_p для Gemini (по умолчанию `1`)
- `GEMINI_TIMEOUT_SECONDS`: Таймаут запроса к Gemini в секундах (по умолчанию `60`, `0` — без таймаута)
- `TRANSCRIPTION_PROVIDER`: Основной провайдер (`elevenlabs` или `gemini`)
- `TRANSCRIPTION_FALLBACKS`: Фолбэк‑провайдеры через запятую (например, `gemini,elevenlabs`)

## Запуск приложения

1. Запустите Redis сервер
2. Запустите Celery воркер:

```bash
celery -A app.tasks worker --loglevel=info
```

3. Запустите FastAPI приложение:

```bash
uvicorn app.main:app --reload
```

Приложение будет доступно по адресу `http://localhost:8000`

## Документация API

В текущей конфигурации приложения Swagger UI и ReDoc отключены
(`docs_url=None`, `redoc_url=None` в `app/main.py`).
Используйте документацию в репозитории:

- Полная карта endpoint'ов + процесс создания пользователя через админа:
  [docs/api-endpoints.md](docs/api-endpoints.md)
- Клиентский гайд по транскрибации:
  [docs/transcription-client-guide.md](docs/transcription-client-guide.md)

## Клиентский гайд: транскрибация (`/transcribe`)

Подробная инструкция вынесена в отдельный файл:
[docs/transcription-client-guide.md](docs/transcription-client-guide.md)

## Разработка

1. Установите зависимости для разработки:

```bash
poetry install --with dev
```

2. Запустите тесты:

```bash
pytest
```

## Продакшн деплой

Пошаговый деплой на Timeweb Cloud VPS через GitHub Actions (с автоматическим TLS через Caddy):

- [DEPLOY_TIMEWEB.md](DEPLOY_TIMEWEB.md)

## Мониторинг

Приложение предоставляет метрики Prometheus по эндпоинту `/metrics`. Основные метрики включают:

- Задержку запросов
- Частоту ошибок
- Длительность обработки аудио
- Использование ресурсов

## Лицензия

[Ваша Лицензия]

## Авторы

- Константин Косолапов (<knets@yandex.ru>)
