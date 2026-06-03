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
DATA_DIR = Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", os.getenv("DATA_DIR", "./data")))
SESSIONS_DIR = DATA_DIR / "sessions"
DEFAULT_SESSION_ID = "main-1206-default"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://akira-main-1206-production.up.railway.app")

app = FastAPI(
    title="Akira Main 1206 API",
    version="1.0.0",
    description="Railway API for Akira Main 1206 session context and state saving.",
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
    return [path for path in paths if repo_file_exists(path)]


def default_current_state() -> Dict[str, Any]:
    return {
        "date": "1206-08-31",
        "time": "02:40",
        "scene_id": "start_scene",
        "location": "jun_house_akira_room",
        "active_characters": ["akira", "jun_carter", "irey", "emma"],
        "nearby_characters": [],
        "conditional_characters": ["raiden_sterling"],
        "akira_state": "резко проснулась; тело напряжено; память держит только последние два года",
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


def classify_mode(req: TurnContractRequest) -> str:
    text = req.user_input.lower()
    if req.mode != "play":
        return req.mode
    technical_markers = [
        "проверь", "проверяй", "репозитор", "github", "railway", "volume", "волум",
        "schema", "openapi", "api", "endpoint", "deploy", "билд", "build", "debug",
        "не продолжай сцену", "техничес", "перенеси", "создай файл", "обнови файл",
    ]
    if any(marker in text for marker in technical_markers):
        return "technical"
    return "play"


def build_required_files(state: Dict[str, Any], mode: str) -> List[str]:
    base = [
        "gpt/engine_prompt.md",
        "gpt/context_loading_policy.md",
        "gpt/scene_format.md",
        "canon/scene_is_not_simulator.md",
        "calendar/story_calendar.md",
    ]
    if mode != "play":
        return existing_files(base)

    scene_id = state.get("scene_id", "start_scene")
    if scene_id == "start_scene":
        base.extend(["data/scenes/start_scene.md", "data/scenes/start_scene_logic.md"])

    character_map = {
        "akira": "characters/main/akira_akatsumi.md",
        "jun_carter": "characters/main/jun_carter.md",
        "irey": "characters/main/irey.md",
        "emma": "characters/main/emma.md",
        "raiden_sterling": "characters/main/raiden_sterling_final.md",
        "yuna": "characters/main/yuna.md",
        "miki": "characters/main/miki_larsen.md",
        "natsu": "characters/main/natsu.md",
    }
    knowledge_map = {
        "akira": "knowledge/characters/akira_knowledge.md",
        "jun_carter": "knowledge/characters/jun_carter_knowledge.md",
        "irey": "knowledge/characters/irey_knowledge.md",
        "emma": "knowledge/characters/emma_knowledge.md",
        "raiden_sterling": "knowledge/characters/raiden_sterling_knowledge.md",
        "yuna": "knowledge/characters/yuna_knowledge.md",
        "miki": "knowledge/characters/miki_knowledge.md",
        "natsu": "knowledge/characters/natsu_knowledge.md",
    }

    active = list(state.get("active_characters") or [])
    nearby = list(state.get("nearby_characters") or [])
    for character_id in dict.fromkeys(active + nearby):
        if character_id in character_map:
            base.append(character_map[character_id])
        if character_id in knowledge_map:
            base.append(knowledge_map[character_id])

    return existing_files(base)


def build_turn_contract(session_id: str, req: TurnContractRequest) -> Dict[str, Any]:
    mode = classify_mode(req)
    is_game_turn = mode == "play"
    state = get_current_state(session_id)
    scene_id = state.get("scene_id", "start_scene")

    return {
        "session_id": safe_session_id(session_id),
        "mode": mode,
        "is_game_turn": is_game_turn,
        "can_generate_scene": True,
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
        "required_files": build_required_files(state, mode),
        "optional_files": [],
        "forbidden_files_or_topics": [
            "do_not_load_inactive_future_characters",
            "do_not_use_hidden_lore_as_npc_knowledge",
            "do_not_simulate_empty_steps",
        ],
        "topic_triggers": ["start_scene", "calendar", "scene_format"],
        "relationship_pairs_needed": [],
        "checks": [
            "do_not_reveal_unknown_names",
            "do_not_introduce_echo_before_calendar_condition",
            "do_not_make_raiden_active_before_condition",
            "do_not_advance_time_for_technical_turn",
            "story_not_simulator_or_sandbox",
        ],
        "message": "Turn contract ready. Load only required_files and obey checks.",
    }


def actions_schema_json() -> Dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Akira Main 1206 API",
            "version": "1.0.0",
            "description": "Railway API for Akira Main 1206 session context and state saving.",
        },
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "healthCheck",
                    "summary": "Check API health",
                    "responses": {"200": {"description": "API is running", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/debug/volume": {
                "get": {
                    "operationId": "debugVolume",
                    "summary": "Check Railway volume",
                    "responses": {"200": {"description": "Railway volume status", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/api/v1/sessions/{session_id}/turn-contract": {
                "post": {
                    "operationId": "getTurnContract",
                    "summary": "Get turn context before generating a scene",
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["user_input", "mode"],
                                    "properties": {
                                        "user_input": {"type": "string"},
                                        "mode": {"type": "string", "enum": ["play", "technical", "audit", "transfer"]},
                                        "client_context": {"type": "object"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Turn contract", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/api/v1/sessions/{session_id}/turn-result": {
                "post": {
                    "operationId": "submitTurnResult",
                    "summary": "Save generated scene and state patches",
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["scene_id", "scene_text", "technical"],
                                    "properties": {
                                        "scene_id": {"type": "string"},
                                        "scene_text": {"type": "string"},
                                        "technical": {"type": "boolean"},
                                        "state_patches": {"type": "object"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Turn result saved", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
        },
    }


def actions_schema_yaml() -> str:
    # Kept intentionally simple for GPT Builder import fallback.
    return f"""
openapi: 3.0.3
info:
  title: Akira Main 1206 API
  version: "1.0.0"
  description: Railway API for Akira Main 1206 session context and state saving.
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
          content:
            application/json:
              schema:
                type: object
  /debug/volume:
    get:
      operationId: debugVolume
      summary: Check Railway volume
      responses:
        "200":
          description: Railway volume status
          content:
            application/json:
              schema:
                type: object
  /api/v1/sessions/{{session_id}}/turn-contract:
    post:
      operationId: getTurnContract
      summary: Get turn context before generating a scene
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
              required:
                - user_input
                - mode
              properties:
                user_input:
                  type: string
                mode:
                  type: string
                  enum: [play, technical, audit, transfer]
                client_context:
                  type: object
      responses:
        "200":
          description: Turn contract
          content:
            application/json:
              schema:
                type: object
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
              required:
                - scene_id
                - scene_text
                - technical
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
          content:
            application/json:
              schema:
                type: object
""".strip()


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "Akira Main 1206 API",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "actions_schema_yaml": "/openapi-actions.yaml",
        "actions_schema_json": "/openapi-actions.json",
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


@app.post("/api/v1/sessions/{session_id}/turn-contract")
def turn_contract(session_id: str, req: TurnContractRequest) -> Dict[str, Any]:
    return build_turn_contract(session_id, req)


@app.post("/api/v1/sessions/{session_id}/turn-result")
def turn_result(session_id: str, req: TurnResultRequest) -> Dict[str, Any]:
    session_dir = ensure_dirs(session_id)
    safe_id = safe_session_id(session_id)

    if req.technical:
        append_jsonl(session_dir / "technical_history.jsonl", {"time": utc_now(), "scene_id": req.scene_id, "text": req.scene_text})
        return {"success": True, "status": "technical_saved", "session_id": safe_id}

    append_jsonl(session_dir / "scene_history.jsonl", {
        "time": utc_now(),
        "scene_id": req.scene_id,
        "scene_text": req.scene_text,
        "state_patches": req.state_patches,
    })

    current_state = get_current_state(session_id)
    patch = req.state_patches.get("current_state_patch") if isinstance(req.state_patches, dict) else None
    if isinstance(patch, dict):
        current_state.update(patch)
        current_state["updated_at"] = utc_now()
        write_json_atomic(session_dir / "current_state.json", current_state)

    write_json_atomic(session_dir / "last_turn_result.json", req.model_dump())
    return {
        "success": True,
        "status": "turn_result_saved",
        "session_id": safe_id,
        "updated_files": ["scene_history.jsonl", "last_turn_result.json", "current_state.json"],
    }


@app.get("/openapi-actions.yaml", response_class=PlainTextResponse)
def openapi_actions_yaml() -> str:
    return actions_schema_yaml() + "\n"


@app.get("/openapi-actions.json")
def openapi_actions_json() -> Dict[str, Any]:
    return actions_schema_json()
