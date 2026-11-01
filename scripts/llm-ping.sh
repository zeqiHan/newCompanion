#!/usr/bin/env bash
# Guide §9 — 30s cloud LLM self-check (does not print the key).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/pipeline"
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

python - <<'PY'
import asyncio
import os
import sys
from pathlib import Path

# Ensure dotenv is loaded the same way as the server
sys.path.insert(0, str(Path(".").resolve()))
from companion.config import load_config
from companion.llm import LlmConfigError, chat, get_llm_settings

load_config()
try:
    s = get_llm_settings()
except LlmConfigError as e:
    print("FAIL:", e)
    print("→ 在项目根目录创建 .env.local，填入 LLM_API_KEY（见 docs/newCompanion-llm-cloud-guide.md）")
    raise SystemExit(1)

print(f"OK settings model={s.model} base={s.base_url}")

async def main() -> None:
    text = await chat(
        [{"role": "user", "content": "只回复：ok"}],
        temperature=0.1,
        max_tokens=8,
    )
    print("OK reply:", repr(text))
    if "ok" not in text.lower() and "OK" not in text:
        print("(内容不是严格 ok，但接口已通)")

asyncio.run(main())
PY
