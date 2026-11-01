# newCompanion

本地 **Persistent Learning Companion**（Mac / Apple Silicon）：
语音交互层 + 学习循环状态层。

北极星见 [`SPEC.md`](SPEC.md)：不是优化 AI 说了多少，而是它是否知道学习者下一步最该发生什么。

## 两层架构

1. **Interaction：** 麦克风 → VAD → STT → LLM → TTS → 角色窗
2. **Learning loop：** `learning_state.yaml`（goal / task / evidence / next_action / intervention）

## 启动

双击 **「小遥xiaoyao」**（自动启动管线 + 右下角悬浮窗），或：

```bash
bash scripts/start-overlay.sh
```

只要管线：

```bash
bash scripts/start-pipeline.sh
open http://127.0.0.1:8765/
```

网页左上角 Memory → **学习循环** 可查看/编辑 Learning State。  
说「我想搞懂 …」会激活学习会话。

## 云端 LLM（JudgmentOS / AI Builders 同款）

完整步骤见 [`docs/newCompanion-llm-cloud-guide.md`](docs/newCompanion-llm-cloud-guide.md)。

```bash
cp env.example .env.local
# 填 LLM_API_KEY
bash scripts/llm-ping.sh   # 应看到 OK reply
```

统一入口：`pipeline/companion/llm.py`（`get_llm_settings` / `chat` / `stream_sentences`）。  
默认 `grok-4-fast` @ `space.ai-builders.com`。TTS 默认系统 `say`（`Tingting`）。

## iPhone

```bash
bash scripts/start-phone.sh
```

打开打印出的 `/phone` 地址。同一 Wi‑Fi 的 `http://局域网IP:8765/phone` 可打字；**麦克风需要 HTTPS**（脚本会尽量起 localtunnel）。

## macOS 语音识别

默认 `stt.backend: mac-speech`。首次授权请在 **终端.app** 运行：

```bash
bash scripts/grant-mac-speech.sh
```

## 换立绘

替换：

- `assets/character/portrait.png`
- `ui/public/portrait.png`
