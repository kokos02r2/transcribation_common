# Deploy to Timeweb Cloud VPS + GitHub Actions

Инструкция для деплоя без `root` пользователя.

## 1. One-time server setup (Timeweb VPS)

ОС: Ubuntu 24.04.

### 1.1 Создайте deploy-пользователя (один раз, под root)

```bash
adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
```

Добавьте публичный ключ в `/home/deploy/.ssh/authorized_keys`:

```bash
nano /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```

### 1.2 Установите Docker

Выполняйте под `deploy` (или под root, но дальше работать лучше от `deploy`):

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
```

Перелогиньтесь пользователем `deploy`.

### 1.3 Подготовьте директорию приложения

```bash
sudo mkdir -p /opt/speech-diarization-common/deploy
sudo chown -R deploy:deploy /opt/speech-diarization-common
```

### 1.4 (Рекомендуется) запретите вход под root по SSH

В `/etc/ssh/sshd_config`:

- `PermitRootLogin no`
- `PasswordAuthentication no` (если используете только ключи)

После изменений:

```bash
sudo systemctl restart ssh
```

## 2. Prepare production env file

На локальной машине:

```bash
cp deploy/.env.prod.example deploy/.env.prod
```

Заполните `deploy/.env.prod`.

Критично:

- `APP_DOMAIN`
- `ACME_EMAIL`
- `DATABASE_URL` (внешний Postgres)
- `REDIS_URL` (внешний Redis)
- `SECRET`
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_PROXY_URL`
- `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_NAME`

Важно: на внешних Postgres/Redis добавьте allowlist для IP вашего Timeweb VPS.

## 3. DNS and firewall

1. Добавьте A-запись: `APP_DOMAIN -> public_ip_vps`.
2. Откройте inbound:
   - `22/tcp`
   - `80/tcp`
   - `443/tcp`

## 4. GitHub Actions secrets

В `Settings -> Secrets and variables -> Actions` добавьте:

- `TIMEWEB_HOST`: IP/hostname VPS
- `TIMEWEB_USER`: `deploy`
- `TIMEWEB_SSH_KEY`: приватный SSH ключ пользователя `deploy`
- `TIMEWEB_SSH_PORT`: обычно `22`
- `GHCR_USERNAME`: GitHub username
- `GHCR_TOKEN`: PAT с правом `read:packages`
- `DEPLOY_ENV_FILE`: полное многострочное содержимое `deploy/.env.prod`

## 5. First deploy

Сделайте push в `main` или запустите workflow вручную.

Workflow делает:

- syntax check (`compileall`)
- build Docker image
- push image в GHCR
- upload `deploy/*` на сервер
- запись `/opt/speech-diarization-common/deploy/.env`
- запуск `deploy/deploy.sh`

## 6. Verify on server

```bash
cd /opt/speech-diarization-common
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml ps
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml logs -f api
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml logs -f worker
```

Проверьте:

- `https://<APP_DOMAIN>/` открывается
- TLS сертификат выпущен автоматически Caddy
- `https://<APP_DOMAIN>/metrics` возвращает `403`
- в `worker` нет ошибок подключения к Redis

## 7. Rollback

Откат на предыдущий image tag (SHA):

```bash
cd /opt/speech-diarization-common
sed -i.bak 's/^IMAGE_TAG=.*/IMAGE_TAG=<previous_sha>/' deploy/.env
bash deploy/deploy.sh <previous_sha>
```
