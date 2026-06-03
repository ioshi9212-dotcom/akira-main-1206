from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION_ID = os.getenv("DEFAULT_SESSION_ID", "main-1206-default")
DEFAULT_PUBLIC_BASE_URL = "https://akira-main-1206-production-c042.up.railway.app"
START_SCENE_FILE = "data/scenes/start_scene.md"
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS", "18000"))
START_COMMANDS = {"начнем", "начнём", "начинай", "старт", "start", "begin"}


def running_on_railway() -> bool:
    return any(os.getenv(key) for key in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"))


def resolve_data_dir() -> Path:
    default_dir = "/data" if running_on_railway() else "./data"
    return Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("DATA_DIR") or default_dir)


def normalize_base_url(value: Optional[str]) -> str:
    if not value:
        return ""
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


DATA_DIR = resolve_data_dir()
SESSIONS_DIR = DATA_DIR / "sessions"
PUBLIC_BASE_URL = normalize_base_url(os.getenv("PUBLIC_BASE_URL")) or normalize_base_url(os.getenv("RAILWAY_PUBLIC_DOMAIN")) or DEFAULT_PUBLIC_BASE_URL

app = FastAPI(
    title="Akira Main 1206 API",
    version="1.4.0",
    description="Railway API for Akira Main 1206: exact start scene output, turn contracts and state saving.",
)


class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, description="Optional session id. Defaults to DEFAULT_SESSION_ID.")
    reset: bool = Field(default=False, description="Reset starter runtime files from repository defaults.")


class TurnContractRequest(BaseModel):
    user_input: str = Field(..., description="User message, player action, or technical request.")
    mode: Literal["play", "technical", "audit", "transfer"] = "play"
    include_file_contents: bool = False
    client_context: Optional[Dict[str, Any]] = None


class ProcessTurnRequest(BaseModel):
    player_input: str = Field(..., description="Player input. Use this for engine-style first turn exact scene output.")
    mode: Literal["play", "technical", "audit", "transfer"] = "play"
    state_patches: Dict[str, Any] = Field(default_factory=dict)


class TurnResultRequest(BaseModel):
    scene_id: str = "scene"
    scene_text: str = ""
    technical: bool = False
    state_patches: Dict[str, Any] = Field(default_factory=dict)


class ApplyTurnResultRequest(BaseModel):
    scene_id: Optional[str] = None
    scene_text: Optional[str] = None
    technical: bool = False
    state_patches: Dict[str, Any] = Field(default_factory=dict)
    current_state_changes: Dict[str, Any] = Field(default_factory=dict)
    story_lines_changes: Dict[str, Any] = Field(default_factory=dict)
    relationship_changes: Dict[str, Any] = Field(default_factory=dict)
    knowledge_changes: Dict[str, Any] = Field(default_factory=dict)
    reputation_changes: Dict[str, Any] = Field(default_factory=dict)
    rumor_changes: Dict[str, Any] = Field(default_factory=dict)
    inventory_changes: Dict[str, Any] = Field(default_factory=dict)
    power_changes: Dict[str, Any] = Field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_session_id(session_id: str) -> str:
    safe = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_")
    return safe or DEFAULT_SESSION_ID


def safe_repo_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="Unsafe or empty path")
    return "/".join(parts)


def ensure_dirs(session_id: str = DEFAULT_SESSION_ID) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_dir = SESSIONS_DIR / safe_session_id(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    text = read_text(path)
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def append_jsonl(path: Path, item: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def repo_file_exists(relative_path: str) -> bool:
    return (APP_ROOT / relative_path).exists()


def existing_files(paths: List[str]) -> List[str]:
    return [path for path in dict.fromkeys(paths) if repo_file_exists(path)]


def read_repo_text(relative_path: str) -> str:
    safe = safe_repo_path(relative_path)
    return read_text(APP_ROOT / safe)


def trimmed(text: str) -> Dict[str, Any]:
    if len(text) <= MAX_FILE_CHARS:
        return {"content": text, "truncated": False, "chars": len(text)}
    return {"content": text[:MAX_FILE_CHARS], "truncated": True, "chars": len(text)}


def extract_first_text_block(markdown: str) -> str:
    marker = "## Текст первого вывода"
    start = markdown.find(marker)
    if start == -1:
        return ""
    next_section = markdown.find("\n## ", start + len(marker))
    section = markdown[start: next_section if next_section != -1 else len(markdown)]
    block_start = section.find("```text")
    if block_start == -1:
        block_start = section.find("```")
    if block_start == -1:
        return ""
    content_start = section.find("\n", block_start)
    block_end = section.find("```", content_start + 1)
    if content_start == -1 or block_end == -1:
        return ""
    return section[content_start + 1:block_end].strip()


def get_start_scene_text() -> str:
    return extract_first_text_block(read_repo_text(START_SCENE_FILE))


def normalized_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().replace("ё", "е").split())


def is_start_command(text: str) -> bool:
    norm = normalized_text(text)
    return norm in {cmd.replace("ё", "е") for cmd in START_COMMANDS}


def default_current_state() -> Dict[str, Any]:
    return {
        "schema": "current_state_v1",
        "project": "akira-main-1206",
        "date": "1206-08-31",
        "time": "02:40",
        "scene_id": "start_scene",
        "current_scene_anchor": START_SCENE_FILE,
        "location": "jun_house_akira_room",
        "active_characters": ["akira", "jun_carter", "irey", "emma"],
        "nearby_characters": [],
        "conditional_characters": ["raiden_sterling"],
        "akira_state": "резко проснулась; тело напряжено; память держит только последние два года",
        "game_started": False,
        "first_scene_delivered": False,
        "turn_counter": 0,
        "compact_every": 15,
        "since_last_compaction": 0,
        "last_compaction_turn": 0,
        "last_compaction_date": None,
        "last_compaction_note": None,
        "updated_at": utc_now(),
    }


def default_story_lines() -> Dict[str, Any]:
    repo_starter = read_json(APP_ROOT / "state/story_lines.json", None)
    if isinstance(repo_starter, dict):
        return repo_starter
    return {
        "schema": "story_lines_v1",
        "project": "akira-main-1206",
        "turn_counter": {
            "total_game_turns": 0,
            "since_last_compaction": 0,
            "compact_every_turns": 15,
            "last_compaction_turn": 0,
            "last_compaction_date": None,
            "last_compaction_note": None,
            "technical_turns_do_not_count": True,
        },
        "next_beats": {"active": [], "resolved": []},
        "compacted_history": [],
    }


def ensure_session_state(session_id: str, reset: bool = False) -> Path:
    sdir = ensure_dirs(session_id)
    current_path = sdir / "current_state.json"
    story_path = sdir / "story_lines.json"
    if reset or not current_path.exists():
        write_json_atomic(current_path, default_current_state())
    if reset or not story_path.exists():
        write_json_atomic(story_path, default_story_lines())
    return sdir


def get_current_state(session_id: str) -> Dict[str, Any]:
    ensure_session_state(session_id)
    return read_json(ensure_dirs(session_id) / "current_state.json", default_current_state())


def save_current_state(session_id: str, state: Dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json_atomic(ensure_dirs(session_id) / "current_state.json", state)


def get_story_lines(session_id: str) -> Dict[str, Any]:
    ensure_session_state(session_id)
    return read_json(ensure_dirs(session_id) / "story_lines.json", default_story_lines())


def save_story_lines(session_id: str, story_lines: Dict[str, Any]) -> None:
    write_json_atomic(ensure_dirs(session_id) / "story_lines.json", story_lines)


def classify_mode_text(user_input: str, explicit_mode: str = "play") -> str:
    text = normalized_text(user_input)
    if explicit_mode != "play":
        return explicit_mode
    technical_markers = [
        "проверь", "проверяй", "репозитор", "github", "railway", "volume", "волум",
        "schema", "openapi", "api", "endpoint", "deploy", "билд", "build", "debug",
        "не продолжай сцену", "техничес", "перенеси", "переноси", "создай файл", "обнови файл",
        "сверь", "сверка", "почини", "структура", "промт", "формат",
    ]
    if any(marker in text for marker in technical_markers):
        return "technical"
    return "play"


def classify_mode(req: TurnContractRequest) -> str:
    return classify_mode_text(req.user_input, req.mode)


def build_required_files(state: Dict[str, Any], mode: str, start_requested: bool) -> List[str]:
    base = [
        "gpt/engine_prompt.md",
        "gpt/context_loading_policy.md",
        "gpt/scene_format.md",
        "canon/scene_is_not_simulator.md",
        "calendar/story_calendar.md",
        "characters/character_id_index.md",
    ]
    if mode != "play":
        return existing_files(base)

    scene_id = state.get("scene_id", "start_scene")
    game_started = bool(state.get("game_started") or state.get("first_scene_delivered"))
    if scene_id == "start_scene" and (game_started or start_requested):
        base.extend([START_SCENE_FILE, "data/scenes/start_scene_logic.md"])

    character_map = {
        "akira": "characters/main/akira_akatsumi.md",
        "jun_carter": "characters/main/jun_carter.md",
        "irey": "characters/main/irey.md",
        "emma": "characters/main/emma.md",
        "raiden_sterling": "characters/main/raiden_sterling_final.md",
        "raiden": "characters/main/raiden_sterling_final.md",
        "ray_carter": "characters/main/ray_carter.md",
        "haru_foster": "characters/main/haru_foster.md",
        "miki_larsen": "characters/main/miki_larsen.md",
        "miki": "characters/main/miki_larsen.md",
        "alex": "characters/main/alex.md",
        "yuna": "characters/main/yuna.md",
        "samuel_sterling": "characters/main/samuel_sterling.md",
    }
    knowledge_map = {
        "akira": "knowledge/characters/akira_knowledge.md",
        "jun_carter": "knowledge/characters/jun_carter_knowledge.md",
        "irey": "knowledge/characters/irey_knowledge.md",
        "emma": "knowledge/characters/emma_knowledge.md",
        "raiden_sterling": "knowledge/characters/raiden_sterling_knowledge.md",
        "raiden": "knowledge/characters/raiden_sterling_knowledge.md",
        "yuna": "knowledge/characters/yuna_knowledge.md",
        "miki_larsen": "knowledge/characters/miki_knowledge.md",
        "miki": "knowledge/characters/miki_knowledge.md",
    }

    active = list(state.get("active_characters") or [])
    nearby = list(state.get("nearby_characters") or [])
    if not start_requested and not game_started and scene_id == "start_scene":
        active = []
        nearby = []

    for character_id in dict.fromkeys(active + nearby):
        if character_id in character_map:
            base.append(character_map[character_id])
        if character_id in knowledge_map:
            base.append(knowledge_map[character_id])

    return existing_files(base)


def get_turn_counts(session_id: str, current_state: Dict[str, Any]) -> Dict[str, int]:
    story_lines = get_story_lines(session_id)
    counter = story_lines.get("turn_counter") if isinstance(story_lines, dict) else {}
    if not isinstance(counter, dict):
        counter = {}
    total = int(counter.get("total_game_turns", current_state.get("turn_counter", 0)) or 0)
    since = int(counter.get("since_last_compaction", current_state.get("since_last_compaction", 0)) or 0)
    every = int(counter.get("compact_every_turns", current_state.get("compact_every", 15)) or 15)
    return {"total_game_turns": total, "since_last_compaction": since, "compact_every_turns": every}


def build_turn_contract(session_id: str, req: TurnContractRequest) -> Dict[str, Any]:
    ensure_session_state(session_id)
    mode = classify_mode(req)
    start_requested = is_start_command(req.user_input)
    is_game_turn = mode == "play"
    state = get_current_state(session_id)
    scene_id = state.get("scene_id", "start_scene")
    game_started = bool(state.get("game_started") or state.get("first_scene_delivered"))

    first_scene_text = ""
    must_output_initial_scene_text = False
    can_generate_scene = is_game_turn
    response_mode = "generate_next_scene_from_contract"

    if is_game_turn and scene_id == "start_scene" and not game_started:
        if start_requested:
            first_scene_text = get_start_scene_text()
            must_output_initial_scene_text = bool(first_scene_text)
            response_mode = "emit_initial_scene_text_verbatim" if first_scene_text else "initial_scene_text_missing"
            can_generate_scene = bool(first_scene_text)
        else:
            can_generate_scene = False
            response_mode = "await_start_command"

    turn_counts = get_turn_counts(session_id, state)
    should_compact = is_game_turn and game_started and turn_counts["since_last_compaction"] >= turn_counts["compact_every_turns"]
    required_files = build_required_files(state, mode, start_requested)

    return {
        "session_id": safe_session_id(session_id),
        "mode": mode,
        "is_game_turn": is_game_turn,
        "start_requested": start_requested,
        "can_generate_scene": can_generate_scene,
        "response_mode": response_mode,
        "must_output_initial_scene_text": must_output_initial_scene_text,
        "initial_scene_text": first_scene_text,
        "current_scene_anchor": {
            "date": state.get("date"),
            "time": state.get("time"),
            "scene_id": scene_id,
            "location": state.get("location"),
            "active_characters": state.get("active_characters", []),
            "nearby_characters": state.get("nearby_characters", []),
            "conditional_characters": state.get("conditional_characters", []),
        },
        "calendar_window": {
            "file": "calendar/story_calendar.md",
            "event_id": scene_id,
            "next_required_event": "raiden_motorcycle_arrival" if scene_id == "start_scene" else None,
        },
        "required_files": required_files,
        "required_file_contents": {path: trimmed(read_repo_text(path)) for path in required_files} if req.include_file_contents else {},
        "optional_files": [],
        "forbidden_files_or_topics": [
            "do_not_load_inactive_future_characters",
            "do_not_use_hidden_lore_as_npc_knowledge",
            "do_not_simulate_empty_steps",
            "do_not_emit_initial_scene_before_start_command",
        ],
        "topic_triggers": ["start_scene", "calendar", "scene_format"],
        "relationship_pairs_needed": [],
        "turn_counter": turn_counts,
        "compact_every": turn_counts["compact_every_turns"],
        "should_compact": should_compact,
        "checks": [
            "emit_initial_scene_text_only_if_user_input_is_start_command",
            "if_response_mode_await_start_command_do_not_generate_scene",
            "if_must_output_initial_scene_text_true_emit_initial_scene_text_verbatim",
            "do_not_reveal_unknown_names",
            "do_not_introduce_echo_before_calendar_condition",
            "do_not_make_raiden_active_before_condition",
            "do_not_advance_time_for_technical_turn",
            "story_not_simulator_or_sandbox",
            "after_scene_call_submitTurnResult_or_applyTurnResult_but_do_not_report_save_status_to_user_unless_user_asks",
        ],
        "message": "Turn contract ready. For first start prefer processTurn; for continuations use required_files and then submitTurnResult/applyTurnResult.",
    }


def normalize_state_patches(req: TurnResultRequest | ApplyTurnResultRequest) -> Dict[str, Any]:
    patches = dict(getattr(req, "state_patches", {}) or {})
    compatibility_pairs = {
        "current_state_patch": getattr(req, "current_state_changes", None),
        "story_line_patches": getattr(req, "story_lines_changes", None),
        "relationship_patches": getattr(req, "relationship_changes", None),
        "knowledge_patches": getattr(req, "knowledge_changes", None),
        "reputation_patches": getattr(req, "reputation_changes", None),
        "rumor_patches": getattr(req, "rumor_changes", None),
        "inventory_patches": getattr(req, "inventory_changes", None),
        "power_state_patches": getattr(req, "power_changes", None),
    }
    for key, value in compatibility_pairs.items():
        if isinstance(value, dict) and value:
            patches[key] = value
    return patches


def mark_game_turn(session_id: str, scene_id: str, scene_text: str, state_patches: Optional[Dict[str, Any]] = None) -> None:
    session_dir = ensure_dirs(session_id)
    current_state = get_current_state(session_id)
    story_lines = get_story_lines(session_id)
    patches = state_patches or {}

    current_patch = patches.get("current_state_patch") if isinstance(patches, dict) else None
    if isinstance(current_patch, dict):
        deep_merge(current_state, current_patch)

    story_patch = patches.get("story_line_patches") if isinstance(patches, dict) else None
    if isinstance(story_patch, dict):
        deep_merge(story_lines, story_patch)

    if scene_id == "start_scene":
        current_state["game_started"] = True
        current_state["first_scene_delivered"] = True

    counter = story_lines.setdefault("turn_counter", {})
    counter["total_game_turns"] = int(counter.get("total_game_turns", current_state.get("turn_counter", 0)) or 0) + 1
    counter["since_last_compaction"] = int(counter.get("since_last_compaction", current_state.get("since_last_compaction", 0)) or 0) + 1
    counter["compact_every_turns"] = int(counter.get("compact_every_turns", current_state.get("compact_every", 15)) or 15)
    counter["updated_at"] = utc_now()

    current_state["turn_counter"] = counter["total_game_turns"]
    current_state["since_last_compaction"] = counter["since_last_compaction"]
    current_state["compact_every"] = counter["compact_every_turns"]
    current_state["last_scene_id"] = scene_id

    append_jsonl(session_dir / "scene_history.jsonl", {"time": utc_now(), "scene_id": scene_id, "scene_text": scene_text, "state_patches": patches})
    save_current_state(session_id, current_state)
    save_story_lines(session_id, story_lines)


def save_turn_result(session_id: str, req: TurnResultRequest | ApplyTurnResultRequest) -> Dict[str, Any]:
    session_dir = ensure_dirs(session_id)
    safe_id = safe_session_id(session_id)
    scene_id = getattr(req, "scene_id", None) or "scene"
    scene_text = getattr(req, "scene_text", None) or ""
    technical = bool(getattr(req, "technical", False))
    patches = normalize_state_patches(req)

    if technical:
        append_jsonl(session_dir / "technical_history.jsonl", {"time": utc_now(), "scene_id": scene_id, "text": scene_text})
        write_json_atomic(session_dir / "last_turn_result.json", {"scene_id": scene_id, "scene_text": scene_text, "technical": technical, "state_patches": patches})
        return {"success": True, "status": "technical_saved", "session_id": safe_id, "updated_files": ["technical_history.jsonl", "last_turn_result.json"]}

    mark_game_turn(session_id, scene_id, scene_text, patches)
    write_json_atomic(session_dir / "last_turn_result.json", {"scene_id": scene_id, "scene_text": scene_text, "technical": technical, "state_patches": patches})
    return {"success": True, "status": "turn_result_saved", "session_id": safe_id, "updated_files": ["scene_history.jsonl", "last_turn_result.json", "current_state.json", "story_lines.json"]}


def object_schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def state_patches_schema() -> Dict[str, Any]:
    return object_schema({
        "current_state_patch": object_schema({}),
        "scene_history_entry": object_schema({}),
        "story_line_patches": object_schema({}),
        "relationship_patches": object_schema({}),
        "knowledge_patches": object_schema({}),
        "inventory_patches": object_schema({}),
        "rumor_patches": object_schema({}),
        "reputation_patches": object_schema({}),
        "power_state_patches": object_schema({}),
        "summary_update": {"type": "string"},
        "compaction_summary": {"type": "string"},
    })


def turn_result_request_schema() -> Dict[str, Any]:
    return object_schema({
        "scene_id": {"type": "string", "default": "scene"},
        "scene_text": {"type": "string"},
        "technical": {"type": "boolean", "default": False},
        "state_patches": state_patches_schema(),
        "current_state_changes": object_schema({}),
        "story_lines_changes": object_schema({}),
        "relationship_changes": object_schema({}),
        "knowledge_changes": object_schema({}),
        "reputation_changes": object_schema({}),
        "rumor_changes": object_schema({}),
        "inventory_changes": object_schema({}),
        "power_changes": object_schema({}),
    }, required=["scene_text"])


def session_parameter() -> Dict[str, Any]:
    return {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}


def actions_schema_json() -> Dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    generic_response = object_schema({"success": {"type": "boolean"}, "status": {"type": "string"}, "message": {"type": "string"}, "time": {"type": "string"}})
    process_turn_response = object_schema({"session_id": {"type": "string"}, "player_input": {"type": "string"}, "current_scene_id": {"type": "string"}, "status": {"type": "string"}, "scene_text": {"type": "string"}})
    turn_contract_response = object_schema({
        "session_id": {"type": "string"},
        "mode": {"type": "string"},
        "is_game_turn": {"type": "boolean"},
        "start_requested": {"type": "boolean"},
        "can_generate_scene": {"type": "boolean"},
        "response_mode": {"type": "string"},
        "must_output_initial_scene_text": {"type": "boolean"},
        "initial_scene_text": {"type": "string"},
        "current_scene_anchor": object_schema({"date": {"type": "string"}, "time": {"type": "string"}, "scene_id": {"type": "string"}, "location": {"type": "string"}, "active_characters": string_array, "nearby_characters": string_array, "conditional_characters": string_array}),
        "calendar_window": object_schema({"file": {"type": "string"}, "event_id": {"type": "string"}, "next_required_event": {"type": "string"}}),
        "required_files": string_array,
        "required_file_contents": object_schema({}),
        "optional_files": string_array,
        "forbidden_files_or_topics": string_array,
        "topic_triggers": string_array,
        "relationship_pairs_needed": string_array,
        "turn_counter": object_schema({"total_game_turns": {"type": "integer"}, "since_last_compaction": {"type": "integer"}, "compact_every_turns": {"type": "integer"}}),
        "compact_every": {"type": "integer"},
        "should_compact": {"type": "boolean"},
        "checks": string_array,
        "message": {"type": "string"},
    })
    turn_result_response = object_schema({"success": {"type": "boolean"}, "status": {"type": "string"}, "session_id": {"type": "string"}, "updated_files": string_array})
    turn_contract_request = object_schema({
        "user_input": {"type": "string"},
        "mode": {"type": "string", "enum": ["play", "technical", "audit", "transfer"], "default": "play"},
        "include_file_contents": {"type": "boolean", "default": False},
        "client_context": object_schema({"last_assistant_message_id": {"type": "string"}, "known_current_scene_anchor": {"type": "string"}}),
    }, required=["user_input"])

    return {
        "openapi": "3.1.0",
        "info": {"title": "Akira Main 1206 API", "version": "1.4.0", "description": "Railway API for Akira Main 1206 exact start scene output and state saving."},
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/health": {"get": {"operationId": "healthCheck", "summary": "Check API health", "responses": {"200": {"description": "API is running", "content": {"application/json": {"schema": generic_response}}}}}},
            "/debug/volume": {"get": {"operationId": "debugVolume", "summary": "Check Railway volume", "responses": {"200": {"description": "Railway volume status"}}}},
            "/api/v1/sessions": {"post": {"operationId": "createSession", "summary": "Create or initialize a runtime session", "requestBody": {"required": False, "content": {"application/json": {"schema": object_schema({"session_id": {"type": "string"}, "reset": {"type": "boolean", "default": False}})}}}, "responses": {"200": {"description": "Session initialized"}}}},
            "/api/v1/turn/context": {"post": {"operationId": "getDefaultTurnContext", "summary": "Get default turn context", "requestBody": {"required": True, "content": {"application/json": {"schema": turn_contract_request}}}, "responses": {"200": {"description": "Turn contract", "content": {"application/json": {"schema": turn_contract_response}}}}}},
            "/api/v1/sessions/{session_id}/turn": {"post": {"operationId": "processTurn", "summary": "Engine-style turn processing. On first start command returns exact start scene text with a slim response.", "parameters": [session_parameter()], "requestBody": {"required": True, "content": {"application/json": {"schema": object_schema({"player_input": {"type": "string"}, "mode": {"type": "string", "enum": ["play", "technical", "audit", "transfer"], "default": "play"}, "state_patches": state_patches_schema()}, required=["player_input"])}}}, "responses": {"200": {"description": "Turn processed", "content": {"application/json": {"schema": process_turn_response}}}}}},
            "/api/v1/sessions/{session_id}/turn-contract": {"post": {"operationId": "getTurnContract", "summary": "Get turn context before generating a scene", "parameters": [session_parameter()], "requestBody": {"required": True, "content": {"application/json": {"schema": turn_contract_request}}}, "responses": {"200": {"description": "Turn contract", "content": {"application/json": {"schema": turn_contract_response}}}}}},
            "/api/v1/sessions/{session_id}/turn-result": {"post": {"operationId": "submitTurnResult", "summary": "Save generated scene and state patches", "parameters": [session_parameter()], "requestBody": {"required": True, "content": {"application/json": {"schema": turn_result_request_schema()}}}, "responses": {"200": {"description": "Turn result saved", "content": {"application/json": {"schema": turn_result_response}}}}}},
            "/api/v1/sessions/{session_id}/apply-turn-result": {"post": {"operationId": "applyTurnResult", "summary": "Compatibility alias for saving generated scene and state changes", "parameters": [session_parameter()], "requestBody": {"required": True, "content": {"application/json": {"schema": turn_result_request_schema()}}}, "responses": {"200": {"description": "Turn result saved", "content": {"application/json": {"schema": turn_result_response}}}}}},
            "/api/v1/files/{file_path}": {"get": {"operationId": "readProjectFile", "summary": "Read a repository file by path", "parameters": [{"name": "file_path", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "File content"}}}},
        },
    }


def actions_schema_yaml() -> str:
    return json.dumps(actions_schema_json(), ensure_ascii=False, indent=2)


@app.get("/")
def root() -> Dict[str, Any]:
    return {"status": "ok", "service": "Akira Main 1206 API", "version": "1.4.0", "docs": "/docs", "openapi": "/openapi.json", "actions_schema_yaml": "/openapi-actions.yaml", "actions_schema_json": "/openapi-actions.json", "start_command": "начнем", "engine_style_start_endpoint": "/api/v1/sessions/{session_id}/turn"}


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"success": True, "status": "ok", "time": utc_now()}


@app.get("/debug/volume")
def debug_volume() -> Dict[str, Any]:
    try:
        ensure_dirs(DEFAULT_SESSION_ID)
        test_file = DATA_DIR / "volume_test.txt"
        test_file.write_text(f"volume works {utc_now()}\n", encoding="utf-8")
        return {"success": True, "mount": str(DATA_DIR), "exists": DATA_DIR.exists(), "sessions_dir": str(SESSIONS_DIR), "test_file": str(test_file), "test_file_exists": test_file.exists(), "files": sorted(p.name for p in DATA_DIR.iterdir())}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/sessions")
def create_session(req: CreateSessionRequest = Body(default=CreateSessionRequest())) -> Dict[str, Any]:
    sid = safe_session_id(req.session_id or DEFAULT_SESSION_ID)
    sdir = ensure_session_state(sid, reset=req.reset)
    return {"success": True, "session_id": sid, "session_dir": str(sdir), "reset": req.reset, "files": sorted(p.name for p in sdir.iterdir()), "next": {"turn": f"/api/v1/sessions/{sid}/turn", "turn_contract": f"/api/v1/sessions/{sid}/turn-contract", "turn_result": f"/api/v1/sessions/{sid}/turn-result"}}


@app.post("/api/v1/turn/context")
def default_turn_context(req: TurnContractRequest) -> Dict[str, Any]:
    return build_turn_contract(DEFAULT_SESSION_ID, req)


@app.post("/api/v1/sessions/{session_id}/turn")
def process_turn(session_id: str, req: ProcessTurnRequest) -> Dict[str, Any]:
    mode = classify_mode_text(req.player_input, req.mode)
    safe_id = safe_session_id(session_id)

    if mode != "play":
        append_jsonl(ensure_dirs(safe_id) / "technical_history.jsonl", {"time": utc_now(), "text": req.player_input})
        return {"session_id": safe_id, "player_input": req.player_input, "current_scene_id": get_current_state(safe_id).get("scene_id"), "status": "TECHNICAL_TURN. Сцена не продолжалась, игровой state не менялся.", "scene_text": ""}

    state = get_current_state(safe_id)
    scene_id = state.get("scene_id", "start_scene")
    game_started = bool(state.get("game_started") or state.get("first_scene_delivered"))

    if scene_id == "start_scene" and not game_started:
        if not is_start_command(req.player_input):
            return {"session_id": safe_id, "player_input": req.player_input, "current_scene_id": scene_id, "status": "AWAIT_START_COMMAND. Напиши 'начнем', чтобы получить стартовую сцену.", "scene_text": ""}

        scene_text = get_start_scene_text()
        if not scene_text:
            return {"session_id": safe_id, "player_input": req.player_input, "current_scene_id": scene_id, "status": "START_SCENE_TEXT_MISSING. Проверь data/scenes/start_scene.md -> ## Текст первого вывода.", "scene_text": ""}

        mark_game_turn(safe_id, "start_scene", scene_text, req.state_patches)
        return {"session_id": safe_id, "player_input": req.player_input, "current_scene_id": "start_scene", "status": "START_SCENE_EXACT_TEXT. Выведи scene_text дословно. Не пересказывай, не переписывай, не продолжай сцену.", "scene_text": scene_text}

    return {"session_id": safe_id, "player_input": req.player_input, "current_scene_id": scene_id, "status": "USE_TURN_CONTRACT. Для продолжения вызови getTurnContract, прочитай required_files, напиши сцену и затем вызови submitTurnResult/applyTurnResult.", "scene_text": ""}


@app.post("/api/v1/sessions/{session_id}/turn-contract")
def turn_contract(session_id: str, req: TurnContractRequest) -> Dict[str, Any]:
    return build_turn_contract(session_id, req)


@app.post("/api/v1/sessions/{session_id}/turn-result")
def turn_result(session_id: str, req: TurnResultRequest) -> Dict[str, Any]:
    return save_turn_result(session_id, req)


@app.post("/api/v1/sessions/{session_id}/apply-turn-result")
def apply_turn_result(session_id: str, req: ApplyTurnResultRequest) -> Dict[str, Any]:
    return save_turn_result(session_id, req)


@app.get("/api/v1/files/{file_path:path}")
def read_file_endpoint(file_path: str) -> Dict[str, Any]:
    path = safe_repo_path(file_path)
    text = read_repo_text(path)
    if not text:
        raise HTTPException(status_code=404, detail="File not found or empty")
    return {"path": path, **trimmed(text)}


@app.get("/openapi-actions.yaml", response_class=PlainTextResponse)
def openapi_actions_yaml() -> str:
    return actions_schema_yaml() + "\n"


@app.get("/openapi-actions.json")
def openapi_actions_json() -> Dict[str, Any]:
    return actions_schema_json()
