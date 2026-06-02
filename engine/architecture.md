# Архитектура движка Akira Main 1206

## Цель

Репозиторий должен работать как память, правила и контекстный слой для интерактивной новеллы.

Railway API хранит состояние, отдаёт нужный контекст и принимает результат хода.

GPT не должен тянуть весь репозиторий. Он должен получать только нужные файлы по текущей сцене, календарю, активным персонажам и темам.

## Важное ограничение про GPT без ключа OpenAI

Если на Railway нет серверного OpenAI API key, сервер не должен пытаться сам вызывать OpenAI.

В таком режиме API работает как context/state backend:

1. клиент / пользовательский GPT запрашивает контекст;
2. API собирает turn-contract и список required_files;
3. GPT пишет сцену на стороне клиента / ChatGPT;
4. клиент отправляет результат хода обратно в API;
5. API сохраняет state, отношения, знания, историю и открытые нитки.

То есть Railway хранит и собирает память, но не обязан сам генерировать текст.

Если позже появится отдельный LLM provider, его можно подключить как дополнительный adapter, не ломая state/storage.

## Главная цепочка хода

```text
POST /api/v1/sessions/{session_id}/turn-contract
→ API читает current_state + calendar + scene + active/mentioned characters
→ API возвращает required_files, checks, knowledge boundaries, relationship snapshot
→ GPT пишет сцену
→ POST /api/v1/sessions/{session_id}/turn-result
→ API сохраняет изменения в volume-backed state
```

## Источник правды

Приоритет:

1. Прямая правка пользователя.
2. `state/current_state.json` или session state в volume.
3. `calendar/story_calendar.md` для дат и окон событий.
4. `data/scenes/<scene_id>.md` для текущей сцены.
5. `characters/main/<id>.md` для поведения персонажа.
6. `knowledge/characters/<id>_knowledge.md` для границ знания.
7. `relationships/pairs/<pair_id>.json` или общий relationships state для динамики.
8. Тематический canon.
9. Hidden lore только как скрытая причинность.
10. История чата не является источником истины, кроме последнего действия игрока.

## Lazy-load принцип

Сервер не грузит все файлы.

Он грузит только:

- текущий state;
- календарь текущей даты / ближайшего окна;
- текущую сцену;
- active/nearby/speaking персонажей;
- mentioned персонажей только если упоминание требует точности;
- topic canon, если текст сцены или запрос содержит тему;
- relationship/preference-файлы только для активных пар.

Пример:

- В сцене сказано `кайросы` → тянуть `canon/kairos_public_and_hidden.md`.
- В сцене есть Райден рядом → тянуть карточку Райдена и его knowledge.
- В сцене Райден только в будущем окне календаря → не тянуть его как активного.
- Акира и Мики разговаривают → тянуть `relationships/pairs/akira__miki.json`, если файл есть.

## Что хранится в репозитории

```text
calendar/                         даты и окна событий
canon/                            лор, правила сцен, контекстная политика
characters/main/                  карточки персонажей
knowledge/characters/             кто что знает / не знает
relationships/pairs/              динамика конкретных пар
relationships/preferences/        вкусы, триггеры, что влияет на метрики
state/templates/                  шаблоны volume-state
engine/                           архитектура и контракты
api_contracts/                    формат запросов/ответов API
```

## Что хранится в Railway Volume

Volume хранит живое состояние сессий, а не статический канон.

```text
/volume/sessions/<session_id>/current_state.json
/volume/sessions/<session_id>/story_lines.json
/volume/sessions/<session_id>/relationships.json
/volume/sessions/<session_id>/knowledge_state.json
/volume/sessions/<session_id>/scene_history.json
/volume/sessions/<session_id>/inventory_state.json
/volume/sessions/<session_id>/reputation_state.json
/volume/sessions/<session_id>/rumors_state.json
/volume/sessions/<session_id>/power_state.json
/volume/sessions/<session_id>/summary.md
```

Нельзя хранить живую сессию только в памяти процесса: Railway может перезапустить контейнер.

## State не должен превращаться в свалку

Не создавать отдельный state-файл под каждую сцену.

Неправильно:

```text
state/akira_met_miki_1206_09_01.json
state/raiden_said_something_1206_09_02.json
```

Правильно:

- событие один раз в `story_lines.json.shared_events`;
- краткая запись в `scene_history.json`;
- изменения знания в `knowledge_state.json`;
- изменения отношений в `relationships.json` или `relationships/pairs/<pair_id>.json`;
- предметы в `inventory_state.json`.

## Отношения

Отношения должны быть отдельным слоем, а не частью карточки персонажа.

Карточка отвечает: кто персонаж.

Knowledge отвечает: что он знает.

Relationships отвечает: как изменилась динамика после сцен.

Preferences отвечает: что влияет на его доверие, уважение, напряжение, ревность и интерес.

## После каждого игрового хода

API должен принять turn-result и обновить только то, что реально изменилось:

- current_state;
- story_lines;
- scene_history;
- relationships;
- knowledge_state;
- inventory;
- rumors;
- reputation;
- power_state;
- summary / compaction.

Технические ходы не меняют игровое состояние.

## Компакция

Каждые 15 игровых ходов делать compaction:

- сжимать мелкие повторы;
- сохранять причины текущих реакций;
- не удалять даты;
- не удалять открытые нитки;
- не терять знание, кто что видел / не видел;
- не стирать отношения и триггеры.

## Связанные файлы

- `engine/load_order.md`
- `engine/lazy_loading_rules.md`
- `api_contracts/turn_contract.md`
- `deployment/railway_volume.md`
- `relationships/relationship_system.md`
- `relationships/preferences/_template.md`
- `state/templates/current_state.template.json`
- `state/templates/story_lines.template.json`