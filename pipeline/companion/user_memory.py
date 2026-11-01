from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_PATH = Path(__file__).resolve().parent / "user_memory.md"

_LOCK = threading.Lock()

DEFAULT_MEMORY = """# 小遥记忆档案

> 你可以随时改这份文档。小遥会在对话里参考「用户画像」和「长期摘要」。

## 用户画像
- 称呼：
- 偏好：
- 不喜欢 / 边界：
- 近况：

## 长期摘要
（尚无摘要。多聊几轮后会自动更新；也可自己写。）

## 备注
（任意补充，小遥会读到。）
"""


def memory_file_path() -> Path:
    return MEMORY_PATH


def ensure_user_memory() -> Path:
    path = MEMORY_PATH
    with _LOCK:
        if not path.exists() or path.stat().st_size < 20:
            path.write_text(DEFAULT_MEMORY, encoding="utf-8")
    return path


def load_user_memory() -> str:
    path = ensure_user_memory()
    with _LOCK:
        return path.read_text(encoding="utf-8")


def save_user_memory(content: str) -> str:
    text = (content or "").strip()
    if not text:
        text = DEFAULT_MEMORY.strip()
    if not text.startswith("#"):
        text = "# 小遥记忆档案\n\n" + text
    path = MEMORY_PATH
    with _LOCK:
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path.read_text(encoding="utf-8")


def memory_for_prompt(*, include_summary: bool = True) -> str:
    """Compact block for the LLM system prompt.

    Profile/notes stay short and useful every turn; long-term summary is optional
    so we don't pay prefill cost on every utterance.
    """
    raw = load_user_memory().strip()
    if not raw:
        return ""
    _, sections = _split_sections(raw)
    parts: list[str] = []
    profile = (sections.get("用户画像") or "").strip()
    notes = (sections.get("备注") or "").strip()
    summary = (sections.get("长期摘要") or "").strip()

    if profile and not _is_blank_profile(profile):
        parts.append("用户画像：\n" + profile)
    if include_summary and summary and not _is_blank_summary(summary):
        parts.append("长期摘要：\n" + summary)
    if notes and not _is_blank_notes(notes):
        parts.append("备注：\n" + notes)
    if not parts:
        return ""
    body = "\n\n".join(parts)
    if len(body) > 1600:
        body = body[:1600] + "\n…"
    return "【记忆｜自然用上即可，勿照读】\n" + body


def _is_blank_profile(text: str) -> bool:
    # Treat the empty template as blank.
    filled = re.sub(r"[-*]\s*[^：:\n]+[：:]\s*", "", text)
    filled = re.sub(r"\s+", "", filled)
    return len(filled) < 2


def _is_blank_summary(text: str) -> bool:
    t = text.strip()
    return (not t) or t.startswith("（尚无") or t.startswith("(尚无")


def _is_blank_notes(text: str) -> bool:
    t = text.strip()
    return (not t) or t.startswith("（任意") or t.startswith("(任意")


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(content: str) -> tuple[str, dict[str, str]]:
    """Return (preamble, {section_title: body})."""
    matches = list(_SECTION_RE.finditer(content))
    if not matches:
        return content, {}
    preamble = content[: matches[0].start()]
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[title] = content[start:end].strip("\n")
    return preamble, sections


def _join_sections(preamble: str, sections: dict[str, str], order: list[str]) -> str:
    parts = [preamble.rstrip()]
    seen = set()
    for title in order:
        if title in sections:
            parts.append(f"## {title}\n{sections[title].strip()}\n")
            seen.add(title)
    for title, body in sections.items():
        if title not in seen:
            parts.append(f"## {title}\n{body.strip()}\n")
    return "\n".join(p for p in parts if p).rstrip() + "\n"


def replace_long_term_summary(summary: str) -> str:
    """Update only the 长期摘要 section; preserve user-edited profile/notes."""
    summary = (summary or "").strip() or "（暂无）"
    with _LOCK:
        content = MEMORY_PATH.read_text(encoding="utf-8") if MEMORY_PATH.exists() else DEFAULT_MEMORY
        preamble, sections = _split_sections(content)
        if not sections:
            content = DEFAULT_MEMORY
            preamble, sections = _split_sections(content)
        sections["长期摘要"] = summary + "\n"
        # Ensure profile/notes keys exist
        sections.setdefault("用户画像", sections.get("用户画像", "- 称呼：\n- 偏好：\n- 不喜欢 / 边界：\n- 近况：\n"))
        sections.setdefault("备注", sections.get("备注", "（任意补充，小遥会读到。）\n"))
        order = ["用户画像", "长期摘要", "备注"]
        text = _join_sections(preamble or "# 小遥记忆档案\n", sections, order)
        MEMORY_PATH.write_text(text, encoding="utf-8")
        return text


def should_refresh_summary(user_turn_count: int, every: int = 4) -> bool:
    return user_turn_count > 0 and user_turn_count % max(1, every) == 0


def build_summary_prompt(history: list[Any], current_doc: str) -> tuple[str, list[Any]]:
    """Return (system, fake_history) for a compact summary completion."""
    recent = history[-12:]
    lines = []
    for turn in recent:
        role = "用户" if turn.role == "user" else "小遥"
        lines.append(f"{role}：{turn.text}")
    dialogue = "\n".join(lines)
    system = (
        "你在维护语音伴侣「小遥」的长期记忆摘要。"
        "根据对话与现有档案，输出一段简洁中文摘要（80～160字）："
        "保留稳定事实、称呼、偏好、情绪线索、未完成事项。"
        "不要列表，不要标题，不要解释，只输出摘要正文。"
    )
    user_text = (
        f"【现有档案】\n{current_doc[:1800]}\n\n"
        f"【最近对话】\n{dialogue}\n\n"
        "请输出更新后的长期摘要："
    )

    class _T:
        def __init__(self, role: str, text: str) -> None:
            self.role = role
            self.text = text

    return system, [_T("user", user_text)]


def companion_memory_payload() -> dict[str, Any]:
    path = ensure_user_memory()
    try:
        relative = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        relative = str(path)
    return {
        "path": str(path),
        "relative": relative,
        "content": load_user_memory(),
    }
