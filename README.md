# akira-main-1206

Railway FastAPI service for the Akira Main 1206 interactive novella state, turn contracts, GPT Actions schema, and persistent session files.

## Что это делает

Сервис хранит состояние сессии на Railway volume и отдаёт GPT Actions контекст перед следующим ходом.

Основные endpoints:

- `GET /health` — проверка, что API живой.
- `GET /debug/volume` — проверка, что volume доступен и туда можно писать.
- `GET /openapi-actions.json` — схема для Custom GPT Actions.
- `GET /openapi-actions.yaml` — та же схема в YAML.
- `POST /api/v1/turn/context` — короткий endpoint для GPT Actions с дефолтной сессией.
- `POST /api/v1/sessions/{session_id}/turn-contract` — получить turn-contract для конкретной сессии.
- `POST /api/v1/sessions/{session_id}/turn-result` — сохранить результат хода и патчи состояния.

## Railway setup

### 1. Подключить GitHub repository

В Railway:

1. New Project.
2. Deploy from GitHub repo.
3. Выбрать `ioshi9212-dotcom/akira-main-1206`.
4. Railway должен увидеть `railway.json` и `Dockerfile`.

В `railway.json` уже указан запуск:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

### 2. Подключить volume

В Railway service:

1. Открыть сервис.
2. Перейти в `Volumes`.
3. Add Volume.
4. Mount path поставить:

```bash
/data
```

### 3. Variables

В Railway service → `Variables` добавить:

```env
RAILWAY_VOLUME_MOUNT_PATH=/data
RAILWAY_PUBLIC_DOMAIN=akira-main-1206-production.up.railway.app
DEFAULT_SESSION_ID=main-1206-default
PROJECT_SLUG=akira-main-1206
START_SCENE_FILE=data/scenes/start_scene.md
```

Если Railway выдаст другой домен, заменить `RAILWAY_PUBLIC_DOMAIN` на свой домен без `https://`.

Можно вместо этого задать полный URL:

```env
PUBLIC_BASE_URL=https://your-domain.up.railway.app
```

`PUBLIC_BASE_URL` имеет приоритет над `RAILWAY_PUBLIC_DOMAIN`.

### 4. Проверить deploy

После redeploy открыть:

```text
https://your-domain.up.railway.app/health
```

Ожидаемый ответ:

```json
{
  "success": true,
  "status": "ok"
}
```

Потом проверить volume:

```text
https://your-domain.up.railway.app/debug/volume
```

Нормально, если ответ содержит:

```json
{
  "success": true,
  "mount": "/data",
  "exists": true,
  "test_file_exists": true
}
```

Если `mount` не `/data`, значит переменная `RAILWAY_VOLUME_MOUNT_PATH` не задана или volume подключён в другое место.

## GPT Actions setup без OpenAI API key

OpenAI API key в Railway для этой схемы не нужен.

Почему: Custom GPT сам вызывает внешний REST API через Actions. Сервер Railway не обращается к OpenAI API, он только отдаёт и сохраняет игровые данные.

Ключ понадобится только если внутри кода Railway появится отдельный вызов OpenAI API, например сервер сам начнёт генерировать сцены через модель.

### Подключение к Custom GPT

1. Открыть свой Custom GPT.
2. Перейти в `Configure`.
3. Открыть `Actions`.
4. Создать Action.
5. Authentication выбрать:

```text
None
```

6. Вставить schema из:

```text
https://your-domain.up.railway.app/openapi-actions.json
```

или открыть YAML:

```text
https://your-domain.up.railway.app/openapi-actions.yaml
```

7. Сохранить Action.

### Какой endpoint лучше использовать GPT

Для обычной работы удобнее:

```text
POST /api/v1/turn/context
```

Он использует дефолтную сессию `DEFAULT_SESSION_ID`.

Для нескольких отдельных сессий использовать:

```text
POST /api/v1/sessions/{session_id}/turn-contract
POST /api/v1/sessions/{session_id}/turn-result
```

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

На Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Локально состояние будет писаться в `./data`, если не задан `DATA_DIR` или `RAILWAY_VOLUME_MOUNT_PATH`.

## Troubleshooting

### GPT Actions не видит schema

Проверить:

```text
https://your-domain.up.railway.app/openapi-actions.json
```

Если страница не открывается, проблема в deploy/domain.

### Volume не сохраняет состояние

Проверить:

```text
https://your-domain.up.railway.app/debug/volume
```

Если `test_file_exists` не `true`, volume не подключён или mount path неверный.

### После redeploy история пропала

Почти всегда причина: данные писались не в volume, а в файловую систему контейнера.

Проверить, что:

```env
RAILWAY_VOLUME_MOUNT_PATH=/data
```

и что Railway volume реально mounted на `/data`.
