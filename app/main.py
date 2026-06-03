from __future__ import annotations

import json
import os
import re
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
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS", "6500"))
START_COMMANDS = {"начнем", "начнём", "начинай", "старт", "start", "begin"}

STATE_FILES = [
    "current_state.json",
    "story_lines.json",
    "relationships.json",
    "knowledge_state.json",
    "reputation_state.json",
    "rumors_state.json",
    "inventory_state.json",
    "power_state.json",
]

BASE_CONTEXT_FILES = [
    "gpt/engine_prompt.md",
    "gpt/context_loading_policy.md",
    "gpt/scene_format.md",
    "canon/scene_is_not_simulator.md",
    "calendar/story_calendar.md",
    "state/current_state.json",
    "state/story_lines.json",
    "state/relationships.json",
    "state/knowledge_state.json",
    "characters/character_id_index.md",
]

CHARACTER_PATHS = {
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

KNOWLEDGE_PATHS = {
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

BEHAVIOR_PROFILES = {
    "akira_version_1_cold": "characters/variants/akira_version_1_cold.md",
    "akira_version_2_chaotic_mask": "characters/variants/akira_version_2_chaotic_mask.md",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    version="1.5.4",
    description="Railway API for Akira Main 1206 with compact day-scoped turn context.",
)


class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = None
    reset: bool = False


class TurnContractRequest(BaseModel):
    user_input: str = Field(...)
    mode: Literal["play", "technical", "audit", "transfer"] = "play"
    include_file_contents: bool = True
    client_context: Optional[Dict[str, Any]] = None


class ProcessTurnRequest(BaseModel):
    player_input: str = Field(...)
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


def safe_session_id(session_id: str) -> str:
    safe = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_")
    return safe or DEFAULT_SESSION_ID


def safe_repo_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="Unsafe or empty path")
    return "/".join(parts)


def session_dir(session_id: str = DEFAULT_SESSION_ID) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / safe_session_id(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def repo_path(path: str) -> Path:
    return APP_ROOT / safe_repo_path(path)


def runtime_state_path(session_id: str, filename: str) -> Path:
    return session_dir(session_id) / filename


def file_exists_for_context(path: str, session_id: str = DEFAULT_SESSION_ID) -> bool:
    safe = safe_repo_path(path)
    if safe.startswith("state/") and runtime_state_path(session_id, Path(safe).name).exists():
        return True
    return repo_path(safe).exists()


def read_project_file(path: str, session_id: str = DEFAULT_SESSION_ID) -> str:
    safe = safe_repo_path(path)
    if safe.startswith("state/"):
        runtime = runtime_state_path(session_id, Path(safe).name)
        if runtime.exists():
            return read_text(runtime)
    return read_text(repo_path(safe))


def trim_text(text: str, limit: Optional[int] = None) -> Dict[str, Any]:
    max_chars = limit or MAX_FILE_CHARS
    if len(text) <= max_chars:
        return {"content": text, "truncated": False, "chars": len(text)}
    return {"content": text[:max_chars], "truncated": True, "chars": len(text)}


def slice_calendar_text(text: str, current_date: str) -> str:
    if not text or not current_date:
        return text
    header = f"# {current_date}"
    date_start = text.find(header)
    if date_start == -1:
        return text[:MAX_FILE_CHARS]

    head = text[:date_start].strip()
    next_date = re.search(r"\n# 1206-\d{2}-\d{2}", text[date_start + 1:])
    date_end = date_start + 1 + next_date.start() if next_date else len(text)
    current_block = text[date_start:date_end].strip()

    common = ""
    common_start = text.find("# Общие запреты календаря")
    if common_start != -1:
        common_end = text.find("## Связанные файлы", common_start)
        common = text[common_start: common_end if common_end != -1 else len(text)].strip()

    return "\n\n---\n\n".join(part for part in [head, current_block, common] if part)


def contract_file_text(path: str, state: Dict[str, Any], session_id: str) -> str:
    text = read_project_file(path, session_id)
    if path == "calendar/story_calendar.md":
        return slice_calendar_text(text, str(state.get("date") or ""))
    return text


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
    return extract_first_text_block(read_project_file(START_SCENE_FILE))


def normalized_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().replace("ё", "е").split())


def is_start_command(text: str) -> bool:
    return normalized_text(text) in {cmd.replace("ё", "е") for cmd in START_COMMANDS}


def detect_behavior_profile(user_input: str) -> Optional[str]:
    text = normalized_text(user_input)
    if "акира версия 2" in text or "акира-2" in text or "версия 2" in text or "вторую версию акиры" in text:
        return "akira_version_2_chaotic_mask"
    if "акира версия 1" in text or "акира-1" in text or "версия 1" in text or "первую версию акиры" in text:
        return "akira_version_1_cold"
    return None


def selected_behavior_profile(user_input: str, state: Dict[str, Any]) -> Optional[str]:
    detected = detect_behavior_profile(user_input)
    if detected:
        return detected
    current = state.get("current_behavior_profile") or state.get("akira_behavior_profile")
    return current if isinstance(current, str) and current in BEHAVIOR_PROFILES else None


def default_current_state() -> Dict[str, Any]:
    starter = read_json(APP_ROOT / "state/current_state.json", None)
    if isinstance(starter, dict):
        starter.setdefault("updated_at", utc_now())
        return starter
    return {
        "schema": "current_state_v1",
        "project": "akira-main-1206",
        "date": "1206-08-31",
        "time": "02:40",
        "scene_id": "start_scene",
        "location": "jun_house_akira_room",
        "active_characters": ["akira", "jun_carter", "irey", "emma"],
        "nearby_characters": [],
        "conditional_characters": ["raiden_sterling"],
        "game_started": False,
        "first_scene_delivered": False,
        "turn_counter": 0,
        "since_last_compaction": 0,
        "compact_every": 15,
        "updated_at": utc_now(),
    }


def default_story_lines() -> Dict[str, Any]:
    starter = read_json(APP_ROOT / "state/story_lines.json", None)
    if isinstance(starter, dict):
        return starter
    return {"schema": "story_lines_v1", "project": "akira-main-1206", "turn_counter": {"total_game_turns": 0, "since_last_compaction": 0, "compact_every_turns": 15}, "next_beats": {"active": [], "resolved": []}}


def ensure_session_state(session_id: str = DEFAULT_SESSION_ID, reset: bool = False) -> Path:
    sdir = session_dir(session_id)
    for filename in STATE_FILES:
        dst = sdir / filename
        src = APP_ROOT / "state" / filename
        if src.exists() and (reset or not dst.exists()):
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    if reset or not (sdir / "current_state.json").exists():
        write_json_atomic(sdir / "current_state.json", default_current_state())
    if reset or not (sdir / "story_lines.json").exists():
        write_json_atomic(sdir / "story_lines.json", default_story_lines())
    return sdir


def current_state(session_id: str = DEFAULT_SESSION_ID) -> Dict[str, Any]:
    ensure_session_state(session_id)
    state = read_json(runtime_state_path(session_id, "current_state.json"), None)
    return state if isinstance(state, dict) else default_current_state()


def story_lines(session_id: str = DEFAULT_SESSION_ID) -> Dict[str, Any]:
    ensure_session_state(session_id)
    data = read_json(runtime_state_path(session_id, "story_lines.json"), {})
    return data if isinstance(data, dict) else {}


def classify_mode(req: TurnContractRequest) -> str:
    if req.mode != "play":
        return req.mode
    text = normalized_text(req.user_input)
    markers = ["github", "railway", "volume", "волум", "api", "openapi", "deploy", "деплой", "schema", "схема", "техничес", "не продолжай сцену", "репозитор", "почини"]
    return "technical" if any(marker in text for marker in markers) else "play"


def build_file_list(state: Dict[str, Any], mode: str, session_id: str, user_input: str = "") -> List[str]:
    files: List[str] = list(BASE_CONTEXT_FILES)
    scene_id = state.get("scene_id", "start_scene")
    started = bool(state.get("game_started") or state.get("first_scene_delivered"))

    if mode == "play" and scene_id == "start_scene" and started:
        files.extend([START_SCENE_FILE, "data/scenes/start_scene_logic.md"])

    if mode == "play":
        active = list(state.get("active_characters") or [])
        nearby = list(state.get("nearby_characters") or state.get("nearby_or_possible") or [])
        for character_id in list(dict.fromkeys(active + nearby)):
            path = CHARACTER_PATHS.get(str(character_id))
            if path:
                files.append(path)
            knowledge = KNOWLEDGE_PATHS.get(str(character_id))
            if knowledge:
                files.append(knowledge)
        profile = selected_behavior_profile(user_input, state)
        if profile:
            files.append(BEHAVIOR_PROFILES[profile])

    return [path for path in dict.fromkeys(files) if file_exists_for_context(path, session_id)]


def get_turn_counts(session_id: str, state: Dict[str, Any]) -> Dict[str, int]:
    counter = story_lines(session_id).get("turn_counter", {})
    if not isinstance(counter, dict):
        counter = {}
    return {
        "total_game_turns": int(counter.get("total_game_turns", state.get("turn_counter", 0)) or 0),
        "since_last_compaction": int(counter.get("since_last_compaction", state.get("since_last_compaction", 0)) or 0),
        "compact_every_turns": int(counter.get("compact_every_turns", state.get("compact_every", 15)) or 15),
    }


def scene_output_contract(started: bool, response_mode: str) -> Dict[str, Any]:
    if response_mode == "emit_initial_scene_text_verbatim":
        return {"mode": "initial_exact_text", "must_output_only_initial_scene_text": True, "do_not_add_header_or_options": True}
    return {
        "mode": "generated_scene",
        "mandatory_source_file": "gpt/scene_format.md",
        "format_is_mandatory": True,
        "must_start_with_scene_header": True,
        "must_not_start_with_technical_comment": True,
        "must_not_write_json_to_user": True,
        "one_short_akira_micro_reply_allowed_if_dialogue_needs_it": True,
        "do_not_play_full_dialogue_for_akira": True,
        "must_check_npc_knowledge_before_each_line": True,
        "stop_on_direct_question_to_akira": True,
        "keep_to_player_action_scale": True,
        "describe_visible_audible_and_npc_actions": True,
        "avoid_poetic_water": True,
        "avoid_romantic_trauma_drama": True,
        "post_start_scene": started,
        "bottom_block_format": "Use old format: Что можно сделать / Что Акира могла бы сказать / Мысли Акиры. Separators on separate lines.",
    }


def build_turn_contract(session_id: str, req: TurnContractRequest) -> Dict[str, Any]:
    ensure_session_state(session_id)
    mode = classify_mode(req)
    state = current_state(session_id)
    start_requested = is_start_command(req.user_input)
    scene_id = state.get("scene_id", "start_scene")
    started = bool(state.get("game_started") or state.get("first_scene_delivered"))

    profile = selected_behavior_profile(req.user_input, state)
    if profile and state.get("current_behavior_profile") != profile:
        state["current_behavior_profile"] = profile
        state["current_behavior_profile_file"] = BEHAVIOR_PROFILES[profile]
        state["updated_at"] = utc_now()
        write_json_atomic(runtime_state_path(session_id, "current_state.json"), state)

    initial_text = ""
    response_mode = "generate_next_scene_from_contract"
    can_generate = mode == "play"
    if mode == "play" and scene_id == "start_scene" and not started:
        if start_requested:
            initial_text = get_start_scene_text()
            response_mode = "emit_initial_scene_text_verbatim" if initial_text else "initial_scene_text_missing"
            can_generate = bool(initial_text)
        else:
            response_mode = "await_start_command"
            can_generate = False

    files = build_file_list(state, mode, session_id, req.user_input)
    counts = get_turn_counts(session_id, state)
    contents = {path: trim_text(contract_file_text(path, state, session_id)) for path in files} if req.include_file_contents else {}

    return {
        "success": True,
        "session_id": safe_session_id(session_id),
        "mode": mode,
        "is_game_turn": mode == "play",
        "start_requested": start_requested,
        "can_generate_scene": can_generate,
        "response_mode": response_mode,
        "must_output_initial_scene_text": bool(initial_text),
        "initial_scene_text": initial_text,
        "current_scene_anchor": {
            "date": state.get("date"),
            "time": state.get("time"),
            "scene_id": scene_id,
            "location": state.get("location"),
            "active_characters": state.get("active_characters", []),
            "nearby_characters": state.get("nearby_characters", []),
            "conditional_characters": state.get("conditional_characters", []),
        },
        "calendar_window": {"file": "calendar/story_calendar.md", "current_date_only": True, "date": state.get("date"), "event_id": scene_id, "next_required_event": "raiden_motorcycle_arrival" if scene_id == "start_scene" else None},
        "calendar_loading_contract": {"active_context_is_current_date_only": True, "future_dates_only_for_timeskip": True, "do_not_use_past_day_goals_after_date_passed": True},
        "akira_behavior_profile": profile,
        "akira_behavior_profile_file": BEHAVIOR_PROFILES.get(profile) if profile else None,
        "required_files": files,
        "required_file_contents": contents,
        "scene_output_contract": scene_output_contract(started, response_mode),
        "turn_counter": counts,
        "compact_every": counts["compact_every_turns"],
        "should_compact": mode == "play" and started and counts["since_last_compaction"] >= counts["compact_every_turns"],
        "checks": [
            "Apply scene_output_contract before writing user-visible scene.",
            "Use calendar only for the current date unless timeskipping.",
            "Use required_file_contents before character behavior.",
            "If akira_behavior_profile_file is present, use it for Akira's behavior style.",
            "Check character knowledge before each NPC line.",
            "One short Akira micro-reply is allowed only if needed; do not play a full dialogue for her.",
            "Stop on direct question to Akira.",
            "After scene output, call submitTurnResult or applyTurnResult.",
        ],
        "message": "Turn context ready. Compact current-date calendar, active character cards, knowledge and scene contract included.",
    }


def normalize_state_patches(req: TurnResultRequest | ApplyTurnResultRequest) -> Dict[str, Any]:
    patches = dict(getattr(req, "state_patches", {}) or {})
    pairs = {
        "current_state_patch": getattr(req, "current_state_changes", None),
        "story_line_patches": getattr(req, "story_lines_changes", None),
        "relationship_patches": getattr(req, "relationship_changes", None),
        "knowledge_patches": getattr(req, "knowledge_changes", None),
        "reputation_patches": getattr(req, "reputation_changes", None),
        "rumor_patches": getattr(req, "rumor_changes", None),
        "inventory_patches": getattr(req, "inventory_changes", None),
        "power_state_patches": getattr(req, "power_changes", None),
    }
    for key, value in pairs.items():
        if isinstance(value, dict) and value:
            patches[key] = value
    return patches


def save_turn_result(session_id: str, req: TurnResultRequest | ApplyTurnResultRequest) -> Dict[str, Any]:
    sdir = ensure_session_state(session_id)
    scene_id = getattr(req, "scene_id", None) or "scene"
    scene_text = getattr(req, "scene_text", None) or ""
    technical = bool(getattr(req, "technical", False))
    patches = normalize_state_patches(req)

    if technical:
        append_jsonl(sdir / "technical_history.jsonl", {"time": utc_now(), "scene_id": scene_id, "text": scene_text})
        return {"success": True, "status": "technical_saved", "session_id": safe_session_id(session_id), "updated_files": ["technical_history.jsonl"]}

    append_jsonl(sdir / "scene_history.jsonl", {"time": utc_now(), "scene_id": scene_id, "scene_text": scene_text, "state_patches": patches})

    state = current_state(session_id)
    current_patch = patches.get("current_state_patch")
    if isinstance(current_patch, dict):
        deep_merge(state, current_patch)
    if scene_id == "start_scene":
        state["game_started"] = True
        state["first_scene_delivered"] = True
    state["updated_at"] = utc_now()
    write_json_atomic(sdir / "current_state.json", state)

    lines = story_lines(session_id)
    counter = lines.setdefault("turn_counter", {})
    counter["total_game_turns"] = int(counter.get("total_game_turns", 0) or 0) + 1
    counter["since_last_compaction"] = int(counter.get("since_last_compaction", 0) or 0) + 1
    counter["last_scene_id"] = scene_id
    counter["updated_at"] = utc_now()
    story_patch = patches.get("story_line_patches")
    if isinstance(story_patch, dict):
        deep_merge(lines, story_patch)
    write_json_atomic(sdir / "story_lines.json", lines)

    write_json_atomic(sdir / "last_turn_result.json", {"scene_id": scene_id, "scene_text": scene_text, "technical": technical, "state_patches": patches})
    return {"success": True, "status": "turn_result_saved", "session_id": safe_session_id(session_id), "updated_files": ["scene_history.jsonl", "current_state.json", "story_lines.json", "last_turn_result.json"]}


def object_schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def request_schema() -> Dict[str, Any]:
    return object_schema({"user_input": {"type": "string"}, "mode": {"type": "string", "enum": ["play", "technical", "audit", "transfer"], "default": "play"}, "include_file_contents": {"type": "boolean", "default": True}, "client_context": {"type": "object"}}, ["user_input"])


def process_turn_schema() -> Dict[str, Any]:
    return object_schema({"player_input": {"type": "string"}, "mode": {"type": "string", "enum": ["play", "technical", "audit", "transfer"], "default": "play"}, "state_patches": {"type": "object"}}, ["player_input"])


def result_schema() -> Dict[str, Any]:
    return object_schema({"scene_id": {"type": "string", "default": "scene"}, "scene_text": {"type": "string"}, "technical": {"type": "boolean", "default": False}, "state_patches": {"type": "object"}, "current_state_changes": {"type": "object"}, "story_lines_changes": {"type": "object"}, "relationship_changes": {"type": "object"}, "knowledge_changes": {"type": "object"}, "reputation_changes": {"type": "object"}, "rumor_changes": {"type": "object"}, "inventory_changes": {"type": "object"}, "power_changes": {"type": "object"}}, ["scene_text"])


def json_body(schema: Dict[str, Any], required: bool = True) -> Dict[str, Any]:
    return {"required": required, "content": {"application/json": {"schema": schema}}}


def session_parameter() -> Dict[str, Any]:
    return {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}


def actions_schema_json() -> Dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Akira Main 1206 API", "version": "1.5.4"},
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/health": {"get": {"operationId": "healthCheck", "summary": "Check API health", "responses": {"200": {"description": "API is running"}}}},
            "/debug/volume": {"get": {"operationId": "debugVolume", "summary": "Check Railway volume", "responses": {"200": {"description": "Volume status"}}}},
            "/api/v1/sessions": {"post": {"operationId": "createSession", "summary": "Create or initialize session", "requestBody": json_body(object_schema({"session_id": {"type": "string"}, "reset": {"type": "boolean", "default": False}}), required=False), "responses": {"200": {"description": "Session initialized"}}}},
            "/api/v1/turn/context": {"post": {"operationId": "getDefaultTurnContext", "summary": "Get default turn context", "requestBody": json_body(request_schema()), "responses": {"200": {"description": "Turn context"}}}},
            "/api/v1/sessions/{session_id}/turn": {"post": {"operationId": "processTurn", "summary": "Process first exact scene turn", "parameters": [session_parameter()], "requestBody": json_body(process_turn_schema()), "responses": {"200": {"description": "Turn processed"}}}},
            "/api/v1/sessions/{session_id}/turn-contract": {"post": {"operationId": "getTurnContract", "summary": "Get turn context before scene generation", "parameters": [session_parameter()], "requestBody": json_body(request_schema()), "responses": {"200": {"description": "Turn context"}}}},
            "/api/v1/sessions/{session_id}/turn-result": {"post": {"operationId": "submitTurnResult", "summary": "Save generated scene", "parameters": [session_parameter()], "requestBody": json_body(result_schema()), "responses": {"200": {"description": "Turn saved"}}}},
            "/api/v1/sessions/{session_id}/apply-turn-result": {"post": {"operationId": "applyTurnResult", "summary": "Compatibility save endpoint", "parameters": [session_parameter()], "requestBody": json_body(result_schema()), "responses": {"200": {"description": "Turn saved"}}}},
            "/api/v1/files/{file_path}": {"get": {"operationId": "readProjectFile", "summary": "Read repository or runtime file", "parameters": [{"name": "file_path", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "File content"}}}},
        },
    }


@app.get("/")
def root() -> Dict[str, Any]:
    return {"status": "ok", "service": "Akira Main 1206 API", "version": "1.5.4", "actions_schema_json": "/openapi-actions.json"}


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"success": True, "status": "ok", "time": utc_now()}


@app.get("/debug/volume")
def debug_volume() -> Dict[str, Any]:
    ensure_session_state(DEFAULT_SESSION_ID)
    test_file = DATA_DIR / "volume_test.txt"
    test_file.write_text(f"volume works {utc_now()}\n", encoding="utf-8")
    return {"success": True, "mount": str(DATA_DIR), "exists": DATA_DIR.exists(), "sessions_dir": str(SESSIONS_DIR), "test_file_exists": test_file.exists(), "session_files": sorted(p.name for p in session_dir(DEFAULT_SESSION_ID).iterdir())}


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
    mode = classify_mode(TurnContractRequest(user_input=req.player_input, mode=req.mode))
    sid = safe_session_id(session_id)
    state = current_state(sid)
    scene_id = state.get("scene_id", "start_scene")
    started = bool(state.get("game_started") or state.get("first_scene_delivered"))

    if mode != "play":
        append_jsonl(session_dir(sid) / "technical_history.jsonl", {"time": utc_now(), "text": req.player_input})
        return {"session_id": sid, "player_input": req.player_input, "current_scene_id": scene_id, "status": "TECHNICAL_TURN", "scene_text": ""}

    if scene_id == "start_scene" and not started:
        if not is_start_command(req.player_input):
            return {"session_id": sid, "player_input": req.player_input, "current_scene_id": scene_id, "status": "AWAIT_START_COMMAND", "scene_text": ""}
        scene_text = get_start_scene_text()
        if not scene_text:
            return {"session_id": sid, "player_input": req.player_input, "current_scene_id": scene_id, "status": "START_SCENE_TEXT_MISSING", "scene_text": ""}
        save_turn_result(sid, TurnResultRequest(scene_id="start_scene", scene_text=scene_text, technical=False, state_patches=req.state_patches))
        return {"session_id": sid, "player_input": req.player_input, "current_scene_id": "start_scene", "status": "START_SCENE_EXACT_TEXT", "scene_text": scene_text}

    return {"session_id": sid, "player_input": req.player_input, "current_scene_id": scene_id, "status": "USE_TURN_CONTRACT", "scene_text": ""}


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
def read_file_endpoint(file_path: str, session_id: str = DEFAULT_SESSION_ID) -> Dict[str, Any]:
    path = safe_repo_path(file_path)
    text = read_project_file(path, session_id)
    if not text:
        raise HTTPException(status_code=404, detail="File not found or empty")
    return {"path": path, **trim_text(text)}


@app.get("/openapi-actions.yaml", response_class=PlainTextResponse)
def openapi_actions_yaml() -> str:
    return json.dumps(actions_schema_json(), ensure_ascii=False, indent=2) + "\n"


@app.get("/openapi-actions.json")
def openapi_actions_json() -> Dict[str, Any]:
    return actions_schema_json()
