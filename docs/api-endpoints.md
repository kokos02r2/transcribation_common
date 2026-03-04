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

### Транскрибация

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/transcribe` | API token (`Bearer`) | Отправить WAV-файл на транскрибацию (асинхронно). |
| GET | `/transcribe/status/{task_id}` | API token (`Bearer`) | Проверить статус и результат задачи. |

Для `POST /transcribe`:
- `multipart/form-data`, поле `file` обязательно.
- Дополнительно: `webhook_url`, `stream_id`, `is_finished`.
- Ограничения: только `.wav`, до `50 MB`, до `15 минут`.

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
