# Engine load order

## Назначение

Этот файл задаёт порядок загрузки контекста для Railway API / GPT.

Не грузить весь репозиторий.

Не делить контекст на огромные блоки `core / characters / memory`.

## Основной порядок

```text
1. session_id
2. volume session state
3. current_scene_anchor
4. calendar текущей даты / окна
5. current scene file
6. active / nearby / speaking characters
7. character knowledge slices
8. relationship pairs / preferences for active pairs
9. topic canon by trigger
10. gpt/scene_format.md
11. gpt/engine_prompt.md
12. gpt/context_loading_policy.md
13. turn-contract response
```

## До игровой сцены

API должен определить:

- текущую дату;
- текущее время;
- место;
- scene_id;
- active characters;
- nearby characters;
- conditional characters;
- последнее действие игрока;
- topic triggers;
- возможные state updates.

Только после этого собирать `required_files`.

## Обязательный минимум

Всегда нужны:

- `gpt/engine_prompt.md`
- `gpt/context_loading_policy.md`
- `gpt/scene_format.md`
- `calendar/story_calendar.md` для текущего окна
- current state из volume
- current scene anchor

## Активные персонажи

Карточки персонажей тянуть только для:

- Акиры;
- тех, кто рядом;
- тех, кто говорит;
- тех, кто действует;
- тех, кто входит в сцену по календарному условию;
- тех, о ком прямо идёт важный разговор.

## Canon по триггерам

- `кайросы`, беловолосые, гибриды, материк, договор → `canon/kairos_public_and_hidden.md`
- энергия, бой, тренировка, перегруз → `canon/energy_system.md`
- пространство Акиры, карман, Эхо, схлопывание → `canon/akira_space_energy_mechanics.md`
- браслет, блокировка, маскировка сигнатуры → `canon/energy_limiters_and_signature_masking.md`
- Восточная база, быт, общежитие, рейды → `canon/east_sector_base.md`
- скрытая причинность Эхо / баланс → `canon/world_hidden_lore.md`

Hidden lore не давать NPC как знание без knowledge.

## После сцены

После игрового ответа API принимает `turn-result` и обновляет только изменившееся:

- current_state;
- scene_history;
- story_lines;
- relationships;
- knowledge_state;
- inventory;
- reputation;
- rumors;
- power_state;
- summary.

Технические ходы не обновляют игровой state.

## Запрет

Не использовать старые lock-файлы как основной скелет.

Если старый lock содержит полезное правило, оно должно быть перенесено в:

- scene logic;
- character knowledge;
- calendar;
- canon;
- scene_format;
- relationship/preferences.

Не создавать новую lock-свалку.