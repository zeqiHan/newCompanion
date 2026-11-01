"""Daily long-horizon loop: one action, evidence, king line, rewards.

The north star stays in learning_state. This file is the scoreboard the
brain can actually feel: a tiny morning commitment, an evening evidence
check, a 14-day wall, and rewards that only fire on output.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .learning_state import load_learning_state, save_learning_state

STATE_PATH = Path(__file__).resolve().parent / "loop_state.yaml"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
WALL_DAYS = 14
BIG_EVERY = 5
_LOCK = threading.Lock()

# Aragorn: the crown is a duty you stand up for today, not a mood you wait for.
KING_LINES = [
    "今天不靠心情登基。王冠是责任，不是情绪。",
    "不是这一天倒下。先做那一件。",
    "游侠可以躲，王要把今天的证据留下。",
    "恐惧可以在，但脚步先走。",
    "血统不会替我完成今天。证据会。",
    "荣耀是事后的名字。事前只有职责。",
    "我可以害怕，但不能把今天交出去。",
    "别人看见王冠，我只看见下一件该做的事。",
    "退回阴影很容易。今天站在该站的位置上。",
    "王的一天从一件小事开始，不是从演说开始。",
    "我不向疲劳投降，我向它借一个最小动作。",
    "若今天只能做一件事，就做那件让明天站得住的事。",
    "王座不是座位，是你愿意为它付出的这一天。",
    "先成为那个留下证据的人，再谈论配不配得上。",
]

DEFAULT_STATE: dict[str, Any] = {
    "focused": False,
    "briefing_credits": 0,
    "rest_window": False,
    "last_week": "",
    "this_week": "",
    "entries": [],
}


def loop_state_path() -> Path:
    return STATE_PATH


def today_local(now: datetime | None = None) -> date:
    current = now or datetime.now(LOCAL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=LOCAL_TZ)
    return current.astimezone(LOCAL_TZ).date()


def phase_for(now: datetime | None = None) -> str:
    current = now or datetime.now(LOCAL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=LOCAL_TZ)
    hour = current.astimezone(LOCAL_TZ).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 21:
        return "day"
    return "evening"


def king_line_for(day: date) -> str:
    return KING_LINES[day.toordinal() % len(KING_LINES)]


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_loop_state() -> dict[str, Any]:
    with _LOCK:
        if not STATE_PATH.exists():
            _write_unlocked(deepcopy(DEFAULT_STATE))
        raw = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    state = deepcopy(DEFAULT_STATE)
    state["focused"] = bool(raw.get("focused"))
    state["briefing_credits"] = int(raw.get("briefing_credits") or 0)
    state["rest_window"] = bool(raw.get("rest_window"))
    state["last_week"] = str(raw.get("last_week") or "")
    state["this_week"] = str(raw.get("this_week") or "")
    entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
    clean: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        day = _parse_day(str(item.get("date") or ""))
        if day is None:
            continue
        clean.append(
            {
                "date": day.isoformat(),
                "action": str(item.get("action") or ""),
                "identity": str(item.get("identity") or ""),
                "evidence": str(item.get("evidence") or ""),
                "has_evidence": bool(item.get("has_evidence")),
                "settled": bool(item.get("settled")),
                "reward": str(item.get("reward") or ""),
                "big_reward": str(item.get("big_reward") or ""),
            }
        )
    clean.sort(key=lambda e: e["date"])
    state["entries"] = clean[-60:]
    return state


def _write_unlocked(state: dict[str, Any]) -> None:
    text = yaml.safe_dump(state, allow_unicode=True, sort_keys=False)
    STATE_PATH.write_text(text, encoding="utf-8")


def save_loop_state(state: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_STATE)
    merged.update(
        {
            "focused": bool(state.get("focused")),
            "briefing_credits": int(state.get("briefing_credits") or 0),
            "rest_window": bool(state.get("rest_window")),
            "last_week": str(state.get("last_week") or ""),
            "this_week": str(state.get("this_week") or ""),
            "entries": list(state.get("entries") or []),
        }
    )
    with _LOCK:
        _write_unlocked(merged)
    return load_loop_state()


def _by_date(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["date"]): e for e in state.get("entries") or []}


def _upsert(state: dict[str, Any], entry: dict[str, Any]) -> None:
    entries = [e for e in state.get("entries") or [] if e.get("date") != entry["date"]]
    entries.append(entry)
    entries.sort(key=lambda e: e["date"])
    state["entries"] = entries[-60:]


def streak_and_scores(state: dict[str, Any], day: date | None = None) -> dict[str, int]:
    """Streak counts settled evidence days. Today does not break it until settled empty."""
    day = day or today_local()
    found = _by_date(state)
    first = None
    for entry in state.get("entries") or []:
        parsed = _parse_day(entry["date"])
        if parsed is not None:
            first = parsed
            break
    if first is None:
        return {"streak": 0, "debt": 0, "compound": 0}

    window_start = max(first, day - timedelta(days=WALL_DAYS - 1))
    debt = 0
    compound = 0
    cursor = window_start
    while cursor < day:
        entry = found.get(cursor.isoformat())
        if entry and entry.get("has_evidence"):
            compound += 1
        elif entry and entry.get("settled"):
            debt += 1
        elif entry is None:
            debt += 1
        cursor += timedelta(days=1)

    today_entry = found.get(day.isoformat())
    if today_entry and today_entry.get("has_evidence"):
        compound += 1
    elif today_entry and today_entry.get("settled"):
        debt += 1

    streak = 0
    # An open day keeps yesterday's streak. A settled day with no evidence breaks it.
    if not (today_entry and today_entry.get("settled") and not today_entry.get("has_evidence")):
        walk = day if today_entry and today_entry.get("has_evidence") else day - timedelta(days=1)
        while True:
            entry = found.get(walk.isoformat())
            if not entry or not entry.get("has_evidence"):
                break
            streak += 1
            walk -= timedelta(days=1)
    return {"streak": streak, "debt": debt, "compound": compound}


def _social_text(day: date, identity: str, action: str, evidence: str, streak: int) -> str:
    shown = evidence.strip() or "（今天还没有证据）"
    return (
        f"今日证据 · {day.isoformat()}\n"
        f"身份：{identity}\n"
        f"动作：{action or '（未定）'}\n"
        f"证据：{shown}\n"
        f"连续有效 {streak} 天"
    )


def loop_view(now: datetime | None = None) -> dict[str, Any]:
    state = load_loop_state()
    day = today_local(now)
    phase = phase_for(now)
    found = _by_date(state)
    today = found.get(day.isoformat()) or {}
    scores = streak_and_scores(state, day)
    identity = str(today.get("identity") or "").strip() or king_line_for(day)
    action = str(today.get("action") or "").strip()
    if not action:
        action = str(load_learning_state().get("next_action") or "").strip()
    evidence = str(today.get("evidence") or "")
    wall = []
    for entry in reversed(state.get("entries") or []):
        parsed = _parse_day(entry["date"])
        if parsed is None or parsed < day - timedelta(days=WALL_DAYS - 1):
            continue
        wall.append(entry)
    return {
        "date": day.isoformat(),
        "phase": phase,
        "identity": identity,
        "king_default": king_line_for(day),
        "action": action,
        "evidence": evidence,
        "settled": bool(today.get("settled")),
        "has_evidence": bool(today.get("has_evidence")),
        "reward": str(today.get("reward") or ""),
        "big_reward": str(today.get("big_reward") or ""),
        "focused": bool(state.get("focused")) and bool(action),
        "briefing_credits": int(state.get("briefing_credits") or 0),
        "rest_window": bool(state.get("rest_window")),
        "last_week": str(state.get("last_week") or ""),
        "this_week": str(state.get("this_week") or ""),
        "streak": scores["streak"],
        "debt": scores["debt"],
        "compound": scores["compound"],
        "wall": wall,
        "social_text": _social_text(day, identity, action, evidence, scores["streak"]),
        "committed_today": bool(str(today.get("action") or "").strip()),
        "ask": _ask_line(
            phase,
            action,
            bool(today.get("settled")),
            bool(str(today.get("action") or "").strip()),
        ),
    }


def _ask_line(phase: str, action: str, settled: bool, committed_today: bool) -> str:
    if phase == "morning" and not committed_today:
        return "早上只定一个 5–25 分钟能做完的动作。"
    if phase == "evening" and not settled:
        return "晚上只问一件事：证据有没有。"
    if action:
        return "工作面只有这一个动作。"
    return "先定今天的唯一动作。"


def commit_morning(
    action: str,
    *,
    identity: str = "",
    last_week: str | None = None,
    this_week: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    action = (action or "").strip()
    if not action:
        raise ValueError("morning action required")
    day = today_local(now)
    state = load_loop_state()
    found = _by_date(state)
    prev = found.get(day.isoformat()) or {}
    line = (identity or "").strip() or str(prev.get("identity") or "").strip() or king_line_for(day)
    entry = {
        "date": day.isoformat(),
        "action": action[:180],
        "identity": line,
        "evidence": str(prev.get("evidence") or ""),
        "has_evidence": bool(prev.get("has_evidence")),
        "settled": bool(prev.get("settled")),
        "reward": str(prev.get("reward") or ""),
        "big_reward": str(prev.get("big_reward") or ""),
    }
    _upsert(state, entry)
    state["focused"] = True
    state["rest_window"] = False
    if last_week is not None:
        state["last_week"] = last_week.strip()
    if this_week is not None:
        state["this_week"] = this_week.strip()
    save_loop_state(state)

    learning = load_learning_state()
    learning["active"] = True
    learning["next_action"] = entry["action"]
    learning["current_task"] = entry["action"]
    if not str(learning.get("goal") or "").strip():
        learning["goal"] = entry["action"]
    save_learning_state(learning)
    return loop_view(now)


def commit_evening(
    has_evidence: bool,
    evidence: str = "",
    *,
    this_week: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    day = today_local(now)
    state = load_loop_state()
    found = _by_date(state)
    prev = found.get(day.isoformat()) or {}
    action = str(prev.get("action") or "").strip() or str(load_learning_state().get("next_action") or "").strip()
    identity = str(prev.get("identity") or "").strip() or king_line_for(day)
    text = (evidence or "").strip()
    if has_evidence and not text:
        raise ValueError("evidence text required")

    # Score as if today is already written, so the streak includes this evidence.
    provisional = deepcopy(state)
    _upsert(
        provisional,
        {
            "date": day.isoformat(),
            "action": action,
            "identity": identity,
            "evidence": text if has_evidence else "",
            "has_evidence": bool(has_evidence),
            "settled": True,
            "reward": "",
            "big_reward": "",
        },
    )
    scores = streak_and_scores(provisional, day)

    reward = ""
    big = ""
    if has_evidence:
        reward = f"证据入墙。连续第 {scores['streak']} 次有效尝试。"
        if scores["streak"] > 0 and scores["streak"] % BIG_EVERY == 0:
            big = "间歇大奖：简报额度 +1，或一段不被计分的休息。今天可以不追加任务。"
            state["briefing_credits"] = int(state.get("briefing_credits") or 0) + 1
            state["rest_window"] = True
    else:
        reward = f"今日无证据。连续清零。债务：补做「{action or '今天的动作'}」。"
        state["rest_window"] = False

    entry = {
        "date": day.isoformat(),
        "action": action,
        "identity": identity,
        "evidence": text if has_evidence else "",
        "has_evidence": bool(has_evidence),
        "settled": True,
        "reward": reward,
        "big_reward": big,
    }
    _upsert(state, entry)
    if this_week is not None:
        state["this_week"] = this_week.strip()
    state["focused"] = bool(action)
    save_loop_state(state)

    learning = load_learning_state()
    learning["active"] = True
    learning["last_attempt"] = action
    learning["evidence"] = text if has_evidence else ""
    if not has_evidence:
        learning["next_action"] = f"补做：{action}" if action else "补交昨天的证据"
        learning["blockers"] = "昨日无证据"
    save_learning_state(learning)
    return loop_view(now)


def set_focus(focused: bool) -> dict[str, Any]:
    state = load_loop_state()
    state["focused"] = bool(focused)
    save_loop_state(state)
    return loop_view()


def loop_for_prompt(now: datetime | None = None) -> str:
    view = loop_view(now)
    lines = [
        f"【长期回路｜{view['phase']}｜勿照读字段】",
        f"身份句：{view['identity']}",
        f"今日唯一动作：{view['action'] or '（还没定）'}",
        f"今日证据：{'有' if view['has_evidence'] else ('无' if view['settled'] else '未结算')}",
        f"连续有效：{view['streak']}  债务：{view['debt']}  复利：{view['compound']}",
        f"上周不会：{view['last_week'] or '—'}",
        f"这周会了：{view['this_week'] or '—'}",
        f"奖励：{view['reward'] or '—'}",
    ]
    if view["big_reward"]:
        lines.append(f"大奖：{view['big_reward']}")
    if view["rest_window"]:
        lines.append("休息窗已开：不要追加新任务。")
    lines.append(
        "早上只帮他定一个 5–25 分钟的动作，并说出身份句。"
        "白天只推这一个动作。"
        "晚上只问证据有没有；有则基于证据肯定一句，无则记下债务、不责骂。"
        "奖励只认证据，不认打开和时长。"
    )
    return "\n".join(lines)


def loop_payload(now: datetime | None = None) -> dict[str, Any]:
    path = STATE_PATH
    try:
        relative = str(path.relative_to(path.parents[2]))
    except ValueError:
        relative = str(path)
    return {"path": str(path), "relative": relative, "loop": loop_view(now)}
