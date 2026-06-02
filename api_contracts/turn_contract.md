# API contract: turn-contract

## Назначение

`turn-contract` — ответ API перед генерацией сцены.

Он говорит GPT, какие файлы нужны именно для этого хода.

Он заменяет чтение полного контекста.

## Endpoint

```text
POST /api/v1/sessions/{session_id}/turn-contract
```

## Input

```json
{
  "user_input": "текст действия игрока или технический запрос",
  "mode": "play | technical | audit | transfer",
  "client_context": {
    "last_assistant_message_id": "optional",
    "known_current_scene_anchor": "optional"
  }
}
```

## Output

```json
{
  "session_id": "main-1206-default",
  "mode": "play",
  "is_game_turn": true,
  "current_scene_anchor": {
    "date": "1206-08-31",
    "time": "02:40",
    "scene_id": "start_scene",
    "location": "jun_house_akira_room",
    "active_characters": ["akira", "jun_carter", "irey", "emma"],
    "nearby_characters": [],
    "conditional_characters": ["raiden_sterling"]
  },
  "calendar_window": {
    "file": "calendar/story_calendar.md",
    "event_id": "start_scene",
    "next_required_event": "raiden_motorcycle_arrival"
  },
  "required_files": [
    "gpt/engine_prompt.md",
    "gpt/context_loading_policy.md",
    "gpt/scene_format.md",
    "data/scenes/start_scene.md",
    "data/scenes/start_scene_logic.md",
    "calendar/story_calendar.md",
    "characters/main/akira_akatsumi.md",
    "characters/main/jun_carter.md",
    "characters/main/irey.md",
    "characters/main/emma.md",
    "knowledge/characters/irey_knowledge.md",
    "knowledge/characters/emma_knowledge.md"
  ],
  "optional_files": [],
  "forbidden_files_or_topics": [
    "do_not_load_inactive_future_characters",
    "do_not_use_hidden_lore_as_npc_knowledge"
  ],
  "topic_triggers": ["start_scene", "kairos", "energy_possible"],
  "relationship_pairs_needed": [
    "akira__irey",
    "akira__emma",
    "jun_carter__irey",
    "jun_carter__emma"
  ],
  "checks": [
    "do_not_reveal_unknown_names",
    "do_not_introduce_echo",
    "do_not_make_raiden_active_before_condition",
    "do_not_advance_time_for_technical_turn"
  ]
}
```

## Правила

- `required_files` должен быть коротким.
- Не включать всех персонажей.
- Не включать все canon-файлы.
- Не включать весь relationships state.
- Hidden lore включать только если нужна скрытая причинность текущего хода.
- Технический ход возвращает `is_game_turn: false`.

## Технический ход

Если `mode = technical`, output должен содержать:

```json
{
  "mode": "technical",
  "is_game_turn": false,
  "state_updates_allowed": false,
  "required_files": [
    "gpt/engine_prompt.md",
    "gpt/context_loading_policy.md"
  ]
}
```

Технический ход не меняет дату, время, scene anchor, отношения или знания.

## Ошибка контекста

Если API не может определить current state:

```json
{
  "error": "missing_current_state",
  "can_generate_scene": false,
  "message": "Не удалось загрузить состояние сессии."
}
```

GPT не должен продолжать сцену уверенно без current state.