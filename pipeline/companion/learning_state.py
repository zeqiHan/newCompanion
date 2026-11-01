from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = Path(__file__).resolve().parent / "learning_state.yaml"

_LOCK = threading.Lock()

DEFAULT_STATE: dict[str, Any] = {
    "active": False,
    "goal": "",
    "current_task": "",
    "current_understanding": "尚未验证",
    "uncertainty": "",
    "blockers": "",
    "last_attempt": "",
    "evidence": "",
    "next_action": "",
    "next_checkpoint": "",
    # answer | probe | verify | redirect | hint | silence
    "intervention": "probe",
    "notes": "",
    "updated_at": "",
}

_START_LEARN_RE = re.compile(
    r"("
    r"开始学习|帮我学|"
    r"我想(学|弄懂|搞懂|搞清楚|搞明白|理解|弄清)|"
    r"我要(学|弄懂|搞懂|搞清楚|搞明白|理解)|"
    r"接下来.{0,16}(想|要)?(学|弄懂|搞懂|学会|搞清楚|理解)|"
    r"今天.{0,12}(学|弄懂|搞懂|搞清楚)|"
    r"(帮我|想要|想把).{0,8}(弄懂|搞懂|搞清楚|学会)"
    r")",
    re.IGNORECASE,
)


def learning_state_path() -> Path:
    return STATE_PATH


def ensure_learning_state() -> Path:
    with _LOCK:
        if not STATE_PATH.exists():
            save_learning_state(DEFAULT_STATE)
    return STATE_PATH


def load_learning_state() -> dict[str, Any]:
    ensure_learning_state()
    with _LOCK:
        raw = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    state = deepcopy(DEFAULT_STATE)
    state.update({k: raw[k] for k in DEFAULT_STATE if k in raw})
    state["active"] = bool(state.get("active"))
    return state


def save_learning_state(state: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_STATE)
    if isinstance(state, dict):
        for k in DEFAULT_STATE:
            if k in state:
                merged[k] = state[k]
    merged["active"] = bool(merged.get("active"))
    merged["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False)
    with _LOCK:
        STATE_PATH.write_text(text, encoding="utf-8")
    return merged


def learning_state_payload() -> dict[str, Any]:
    path = ensure_learning_state()
    try:
        relative = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        relative = str(path)
    state = load_learning_state()
    return {"path": str(path), "relative": relative, "state": state}


def looks_like_start_learn(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(_START_LEARN_RE.search(t))


def activate_from_utterance(text: str) -> dict[str, Any]:
    """Best-effort bootstrap when user declares a learning goal aloud."""
    state = load_learning_state()
    state["active"] = True
    goal = (text or "").strip()
    # Soft trim prefixes
    goal = re.sub(r"^(小遥[，,]\s*)?", "", goal)
    state["goal"] = goal[:120]
    state["current_task"] = "先说清你目前的理解"
    state["current_understanding"] = "尚未验证"
    state["next_action"] = "用自己的话解释你现在懂到哪"
    state["next_checkpoint"] = "听完解释后出一道最小验证题"
    state["intervention"] = "probe"
    state["last_attempt"] = ""
    state["evidence"] = ""
    state["uncertainty"] = ""
    state["blockers"] = ""
    return save_learning_state(state)


def learning_state_for_prompt(state: dict[str, Any] | None = None) -> str:
    s = state or load_learning_state()
    if not s.get("active"):
        return (
            "【学习循环｜当前未激活】\n"
            "若用户明确说想学/搞懂某主题，进入 learn：先锁定目标，再给最小下一步。"
            "不要主动开课。"
        )
    lines = [
        "【学习循环｜已激活｜维护状态，勿照读字段名】",
        f"goal: {s.get('goal') or '（未写）'}",
        f"current_task: {s.get('current_task') or '（未写）'}",
        f"understanding: {s.get('current_understanding') or '尚未验证'}",
        f"uncertainty: {s.get('uncertainty') or '—'}",
        f"blockers: {s.get('blockers') or '—'}",
        f"last_attempt: {s.get('last_attempt') or '—'}",
        f"evidence: {s.get('evidence') or '—'}",
        f"next_action: {s.get('next_action') or '—'}",
        f"next_checkpoint: {s.get('next_checkpoint') or '—'}",
        f"suggested_intervention: {s.get('intervention') or 'probe'}",
        "规则：优先推动 next_action / next_checkpoint；可用 verify 检验是否真懂；"
        "用户只说「懂了」不算 evidence。必要时短沉默式提醒目标，不要长篇讲课。",
    ]
    return "\n".join(lines)


def build_state_update_prompt(
    state: dict[str, Any],
    user_text: str,
    assistant_text: str,
) -> tuple[str, list[Any]]:
    system = (
        "你在维护桌面学习伴侣的 Learning State。"
        "根据本轮对话，输出【仅一段 JSON 对象】，不要 markdown，不要解释。"
        "字段：active(bool), goal, current_task, current_understanding, uncertainty, "
        "blockers, last_attempt, evidence, next_action, next_checkpoint, "
        "intervention(answer|probe|verify|redirect|hint|silence), notes。"
        "evidence 只能写可观察证据（用户解释/答题/代码结果），不要把「他说懂了」当证据。"
        "若用户在闲聊且与学习无关，可保持原 goal，把 intervention 设为 silence。"
        "next_action 必须是一个最小可执行动作。"
    )
    user = (
        f"【当前状态】\n{json.dumps(state, ensure_ascii=False)}\n\n"
        f"【用户】{user_text}\n"
        f"【小遥】{assistant_text}\n\n"
        "输出更新后的 JSON："
    )

    class _T:
        def __init__(self, role: str, text: str) -> None:
            self.role = role
            self.text = text

    return system, [_T("user", user)]


def parse_state_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Strip accidental fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Find first {...}
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def merge_state_update(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(current)
    for k in DEFAULT_STATE:
        if k == "updated_at":
            continue
        if k in patch and patch[k] is not None:
            out[k] = patch[k]
    allowed = {"answer", "probe", "verify", "redirect", "hint", "silence"}
    if str(out.get("intervention")) not in allowed:
        out["intervention"] = "probe"
    return save_learning_state(out)
