from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SLUG = os.getenv("PROJECT_SLUG", "akira-main-1206")
DEFAULT_SESSION_ID = os.getenv("DEFAULT_SESSION_ID", "main-1206-default")
START_SCENE_FILE = os.getenv("START_SCENE_FILE", "data/scenes/start_scene.md")
DEFAULT_PUBLIC_BASE_URL = "https://akira-main-1206-production.up.railway.app"


def running_on_railway() -> bool:
    return any(
        os.getenv(key)
        for key in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_DEPLOYMENT_ID",
        )
    )


def resolve_data_dir() -> Path:
    """Pick the persistent state directory.

    Railway mounts the volume wherever we set the mount path in the dashboard.
    For this project the expected mount path is /data. Locally we keep ./data.
    """

    default_data_dir = "/data" if running_on_railway() else "./data"
    return Path(
        os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        or os.getenv("DATA_DIR")
        or default_data_dir
    )


def normalize_base_url(value: Optional[str]) -> str:
    if not value:
        return ""
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def resolve_public_base_url() -> str:
    return (
        normalize_base_url(os.getenv("PUBLIC_BASE_URL"))
        or normalize_base_url(os.getenv("RAILWAY_PUBLIC_DOMAIN"))
        or DEFAULT_PUBLIC_BASE_URL
    )


DATA_DIR = resolve_data_dir()
SESSIONS_DIR = DATA_DIR / "sessions"
PUBLIC_BASE_URL = resolve_public_base_url()

START_COMMANDS = {
    "начнем",
    "начнём",
    "начинай",
    "старт",
    "start",
    "begin",
}

app = FastAPI(
    title=f"{PROJECT_SLUG} API",
    version="1.2.0",
    description="Railway API for Akira session context, GPT Actions, and persistent state saving.",
)


class TurnContractRequest(BaseModel):
    user_input: str = Field(..., description="User message, player action, or technical request.")
    mode: Literal["play", "technical", "audit", "transfer"] = "play"
    client_context: Optional[Dict[str, Any]] = None


class TurnResultRequest(BaseModel):
    scene_id: str
    scene_text: str
    technical: bool = False
    state_patches: Dict[str, Any] = Field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_session_id(session_id: str) -> str:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    return safe or DEFAULT_SESSION_ID


def ensure_dirs(session_id: str = DEFAULT_SESSION_ID) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_dir = SESSIONS_DIR / safe_session_id(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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


def repo_file_exists(relative_path: str) -> bool:
    return (APP_ROOT / relative_path).exists()


def existing_files(paths: List[str]) -> List[str]:
    return [path for path in dict.fromkeys(paths) if repo_file_exists(path)]


def read_repo_text(relative_path: str) -> str:
    path = APP_ROOT / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def extract_first_text_block(markdown: str) -> str:
    marker = "## Текст первого вывода"
    start = markdown.find(marker)
    if start == -1:
        return ""
    block_start = markdown.find("```text", start)
    if block_start == -1:
        return ""
    content_start = markdown.find("\n", block_start)
    if content_start == -1:
        return ""
    block_end = markdown.find("```", content_start + 1)
    if block_end == -1:
        return ""
    return markdown[content_start + 1:block_end].strip()


def get_start_scene_text() -> str:
    return extract_first_text_block(read_repo_text(START_SCENE_FILE))


def normalized_text(text: str) -> str:
    return " ".join(text.strip().lower().replace("ё", "е").split())


def is_start_command(text: str) -> bool:
    norm = normalized_text(text)
    return norm in {cmd.replace("ё", "е") for cmd in START_COMMANDS}


def default_current_state() -> Dict[str, Any]:
    return {
        "schema": "current_state_v1",
        "project": PROJECT_SLUG,
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


def get_current_state(session_id: str) -> Dict[str, Any]:
    session_dir = ensure_dirs(session_id)
    path = session_dir / "current_state.json"
    state = read_json(path, None)
    if state is None:
        state = default_current_state()
        write_json_atomic(path, state)
    return state


def save_current_state(session_id: str, state: Dict[str, Any]) -> None:
    session_dir = ensure_dirs(session_id)
    state["updated_at"] = utc_now()
    write_json_atomic(session_dir / "current_state.json", state)


def get_story_lines(session_id: str) -> Dict[str, Any]:
    session_dir = ensure_dirs(session_id)
    path = session_dir / "story_lines.json"
    state = read_json(path, None)
    if state is None:
        repo_starter = read_json(APP_ROOT / "state/story_lines.json", None)
        state = repo_starter if isinstance(repo_starter, dict) else {
            "turn_counter": {
                "total_game_turns": 0,
                "since_last_compaction": 0,
                "compact_every_turns": 15,
            }
        }
        write_json_atomic(path, state)
    return state


def save_story_lines(session_id: str, story_lines: Dict[str, Any]) -> None:
    session_dir = ensure_dirs(session_id)
    write_json_atomic(session_dir / "story_lines.json", story_lines)


def classify_mode(req: TurnContractRequest) -> str:
    text = normalized_text(req.user_input)
    if req.mode != "play":
        return req.mode
    technical_markers = [
        "проверь", "проверяй", "репозитор", "github", "railway", "volume", "волум",
        "schema", "openapi", "api", "endpoint", "deploy", "билд", "build", "debug",
        "не продолжай сцену", "техничес", "перенеси", "переноси", "создай файл", "обнови файл",
        "сверь", "сверка", "почини", "структура", "промт", "формат",
    ]
    if any(marker in text for marker in technical_markers):
        return "technical"
    return "play"


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
        "akira_akatsumi": "characters/main/akira_akatsumi.md",
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
        "akira_akatsumi": "knowledge/characters/akira_knowledge.md",
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
    counter = story_lines.get("turn_counter") if isinstance(story_lines, dict) else None
    if not isinstance(counter, dict):
        counter = {}
    total = int(counter.get("total_game_turns", current_state.get("turn_counter", 0)) or 0)
    since = int(counter.get("since_last_compaction", current_state.get("since_last_compaction", 0)) or 0)
    every = int(counter.get("compact_every_turns", current_state.get("compact_every", 15)) or 15)
    return {"total_game_turns": total, "since_last_compaction": since, "compact_every_turns": every}


def build_turn_contract(session_id: str, req: TurnContractRequest) -> Dict[str, Any]:
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
            response_mode = "emit_initial_scene_text_verbatim"
        else:
            can_generate_scene = False
            response_mode = "await_start_command"

    turn_counts = get_turn_counts(session_id, state)
    should_compact = is_game_turn and game_started and turn_counts["since_last_compaction"] >= turn_counts["compact_every_turns"]

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
        "required_files": build_required_files(state, mode, start_requested),
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
        ],
        "message": "Turn contract ready. Initial scene is emitted only after command 'начнем' / 'начнём'.",
    }


def object_schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def actions_schema_json() -> Dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    generic_response = object_schema({
        "success": {"type": "boolean"},
        "status": {"type": "string"},
        "message": {"type": "string"},
        "time": {"type": "string"},
    })
    turn_contract_request = object_schema({
        "user_input": {"type": "string"},
        "mode": {"type": "string", "enum": ["play", "technical", "audit", "transfer"]},
        "client_context": object_schema({
            "last_assistant_message_id": {"type": "string"},
            "known_current_scene_anchor": {"type": "string"},
        }),
    }, required=["user_input", "mode"])
    turn_contract_response = object_schema({
        "session_id": {"type": "string"},
        "mode": {"type": "string"},
        "is_game_turn": {"type": "boolean"},
        "start_requested": {"type": "boolean"},
        "can_generate_scene": {"type": "boolean"},
        "response_mode": {"type": "string"},
        "must_output_initial_scene_text": {"type": "boolean"},
        "initial_scene_text": {"type": "string"},
        "current_scene_anchor": object_schema({
            "date": {"type": "string"},
            "time": {"type": "string"},
            "scene_id": {"type": "string"},
            "location": {"type": "string"},
            "active_characters": string_array,
            "nearby_characters": string_array,
            "conditional_characters": string_array,
        }),
        "calendar_window": object_schema({
            "file": {"type": "string"},
            "event_id": {"type": "string"},
            "next_required_event": {"type": "string"},
        }),
        "required_files": string_array,
        "optional_files": string_array,
        "forbidden_files_or_topics": string_array,
        "topic_triggers": string_array,
        "relationship_pairs_needed": string_array,
        "turn_counter": object_schema({
            "total_game_turns": {"type": "integer"},
            "since_last_compaction": {"type": "integer"},
            "compact_every_turns": {"type": "integer"},
        }),
        "compact_every": {"type": "integer"},
        "should_compact": {"type": "boolean"},
        "checks": string_array,
        "message": {"type": "string"},
    })
    turn_result_request = object_schema({
        "scene_id": {"type": "string"},
        "scene_text": {"type": "string"},
        "technical": {"type": "boolean"},
        "state_patches": object_schema({
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
        }),
    }, required=["scene_id", "scene_text", "technical"])
    turn_result_response = object_schema({
        "success": {"type": "boolean"},
        "status": {"type": "string"},
        "session_id": {"type": "string"},
        "updated_files": string_array,
    })

    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{PROJECT_SLUG} API",
            "version": "1.2.0",
            "description": "Railway API for Akira session context, GPT Actions, and persistent state saving.",
        },
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "healthCheck",
                    "summary": "Check API health",
                    "responses": {"200": {"description": "API is running", "content": {"application/json": {"schema": generic_response}}}},
                }
            },
            "/debug/volume": {
                "get": {
                    "operationId": "debugVolume",
                    "summary": "Check Railway volume",
                    "responses": {"200": {"description": "Railway volume status", "content": {"application/json": {"schema": object_schema({
                        "success": {"type": "boolean"},
                        "mount": {"type": "string"},
                        "exists": {"type": "boolean"},
                        "sessions_dir": {"type": "string"},
                        "test_file": {"type": "string"},
                        "test_file_exists": {"type": "boolean"},
                        "files": string_array,
                    })}}}},
                }
            },
            "/api/v1/turn/context": {
                "post": {
                    "operationId": "getTurnContext",
                    "summary": "Get default-session turn context before generating a scene",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": turn_contract_request}}},
                    "responses": {"200": {"description": "Turn context", "content": {"application/json": {"schema": turn_contract_response}}}},
                }
            },
            "/api/v1/sessions/{session_id}/turn-contract": {
                "post": {
                    "operationId": "getTurnContract",
                    "summary": "Get session turn context before generating a scene",
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": turn_contract_request}}},
                    "responses": {"200": {"description": "Turn contract", "content": {"application/json": {"schema": turn_contract_response}}}},
                }
            },
            "/api/v1/sessions/{session_id}/turn-result": {
                "post": {
                    "operationId": "submitTurnResult",
                    "summary": "Save generated scene and state patches",
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": turn_result_request}}},
                    "responses": {"200": {"description": "Turn result saved", "content": {"application/json": {"schema": turn_result_response}}}},
                }
            },
        },
    }


def actions_schema_yaml() -> str:
    return f"""
openapi: 3.1.0
info:
  title: {PROJECT_SLUG} API
  version: "1.2.0"
  description: Railway API for Akira session context, GPT Actions, and persistent state saving.
servers:
  - url: {PUBLIC_BASE_URL}
paths:
  /health:
    get:
      operationId: healthCheck
      summary: Check API health
      responses:
        "200":
          description: API is running
  /debug/volume:
    get:
      operationId: debugVolume
      summary: Check Railway volume
      responses:
        "200":
          description: Railway volume status
  /api/v1/turn/context:
    post:
      operationId: getTurnContext
      summary: Get default-session turn context before generating a scene
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [user_input, mode]
              properties:
                user_input:
                  type: string
                mode:
                  type: string
                  enum: [play, technical, audit, transfer]
      responses:
        "200":
          description: Turn context
  /api/v1/sessions/{{session_id}}/turn-contract:
    post:
      operationId: getTurnContract
      summary: Get session turn context before generating a scene
      parameters:
        - name: session_id
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [user_input, mode]
              properties:
                user_input:
                  type: string
                mode:
                  type: string
                  enum: [play, technical, audit, transfer]
      responses:
        "200":
          description: Turn contract
  /api/v1/sessions/{{session_id}}/turn-result:
    post:
      operationId: submitTurnResult
      summary: Save generated scene and state patches
      parameters:
        - name: session_id
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [scene_id, scene_text, technical]
              properties:
                scene_id:
                  type: string
                scene_text:
                  type: string
                technical:
                  type: boolean
                state_patches:
                  type: object
      responses:
        "200":
          description: Turn result saved
""".strip()


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": f"{PROJECT_SLUG} API",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "actions_schema_yaml": "/openapi-actions.yaml",
        "actions_schema_json": "/openapi-actions.json",
        "start_command": "начнем",
        "public_base_url": PUBLIC_BASE_URL,
        "data_dir": str(DATA_DIR),
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"success": True, "status": "ok", "time": utc_now()}


@app.get("/debug/volume")
def debug_volume() -> Dict[str, Any]:
    try:
        ensure_dirs(DEFAULT_SESSION_ID)
        test_file = DATA_DIR / "volume_test.txt"
        test_file.write_text(f"volume works {utc_now()}\n", encoding="utf-8")
        return {
            "success": True,
            "mount": str(DATA_DIR),
            "exists": DATA_DIR.exists(),
            "sessions_dir": str(SESSIONS_DIR),
            "test_file": str(test_file),
            "test_file_exists": test_file.exists(),
            "files": sorted(p.name for p in DATA_DIR.iterdir()),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/turn/context")
def turn_context(req: TurnContractRequest) -> Dict[str, Any]:
    return build_turn_contract(DEFAULT_SESSION_ID, req)


@app.post("/api/v1/sessions/{session_id}/turn-contract")
def turn_contract(session_id: str, req: TurnContractRequest) -> Dict[str, Any]:
    return build_turn_contract(session_id, req)


@app.post("/api/v1/sessions/{session_id}/turn-result")
def turn_result(session_id: str, req: TurnResultRequest) -> Dict[str, Any]:
    session_dir = ensure_dirs(session_id)
    safe_id = safe_session_id(session_id)

    if req.technical:
        append_jsonl(session_dir / "technical_history.jsonl", {"time": utc_now(), "scene_id": req.scene_id, "text": req.scene_text})
        return {"success": True, "status": "technical_saved", "session_id": safe_id, "updated_files": ["technical_history.jsonl"]}

    append_jsonl(session_dir / "scene_history.jsonl", {
        "time": utc_now(),
        "scene_id": req.scene_id,
        "scene_text": req.scene_text,
        "state_patches": req.state_patches,
    })

    current_state = get_current_state(session_id)
    story_lines = get_story_lines(session_id)

    patch = req.state_patches.get("current_state_patch") if isinstance(req.state_patches, dict) else None
    if isinstance(patch, dict):
        current_state.update(patch)

    if req.scene_id == "start_scene":
        current_state["game_started"] = True
        current_state["first_scene_delivered"] = True

    counter = story_lines.setdefault("turn_counter", {})
    counter["total_game_turns"] = int(counter.get("total_game_turns", current_state.get("turn_counter", 0)) or 0) + 1
    counter["since_last_compaction"] = int(counter.get("since_last_compaction", current_state.get("since_last_compaction", 0)) or 0) + 1
    counter["compact_every_turns"] = int(counter.get("compact_every_turns", current_state.get("compact_every", 15)) or 15)

    compaction_summary = None
    if isinstance(req.state_patches, dict):
        compaction_summary = req.state_patches.get("compaction_summary")
    if counter["since_last_compaction"] >= counter["compact_every_turns"] and compaction_summary:
        story_lines.setdefault("compacted_history", []).append({
            "turn": counter["total_game_turns"],
            "date": current_state.get("date"),
            "time": current_state.get("time"),
            "summary": compaction_summary,
        })
        counter["last_compaction_turn"] = counter["total_game_turns"]
        counter["last_compaction_date"] = current_state.get("date")
        counter["last_compaction_note"] = compaction_summary
        counter["since_last_compaction"] = 0

    current_state["turn_counter"] = counter["total_game_turns"]
    current_state["since_last_compaction"] = counter["since_last_compaction"]
    current_state["compact_every"] = counter["compact_every_turns"]

    save_current_state(session_id, current_state)
    save_story_lines(session_id, story_lines)
    write_json_atomic(session_dir / "last_turn_result.json", req.model_dump())

    return {
        "success": True,
        "status": "turn_result_saved",
        "session_id": safe_id,
        "updated_files": ["scene_history.jsonl", "last_turn_result.json", "current_state.json", "story_lines.json"],
    }


@app.get("/openapi-actions.yaml", response_class=PlainTextResponse)
def openapi_actions_yaml() -> str:
    return actions_schema_yaml() + "\n"


@app.get("/openapi-actions.json")
def openapi_actions_json() -> Dict[str, Any]:
    return actions_schema_json()
