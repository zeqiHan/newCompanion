# newCompanion

本地语音角色伴侣（Mac / Apple Silicon）：麦克风 → Silero VAD → Whisper STT → LLM → Qwen3-TTS，叠加半屏 / 右下角角色窗。

## 已安装（本机）

| 组件 | 状态 | 位置 |
|------|------|------|
| uv + Python 3.12 | ✅ | `~/.local` + `pipeline/.venv` |
| Silero VAD v5 | ✅ | pip `silero-vad` |
| Whisper tiny (MLX) | ✅ | `models/whisper-tiny-mlx` (~80MB) |
| LLM Qwen3-4B 4bit (MLX) | ✅ | `models/Qwen3-4B-4bit` (~2.3GB) |
| llama.cpp server/cli | ✅ | `tools/llama.cpp/llama-b10375` |
| LLM GGUF (llama.cpp) | ✅ | `models/gguf/qwen2.5-1.5b-instruct-q4_k_m.gguf` (~1GB) |
| Qwen3-TTS 0.6B 8bit (MLX) | ✅ | `models/Qwen3-TTS-0.6B-CustomVoice-8bit` (~1.8GB) |
| 角色立绘 | ✅ | `assets/character/portrait.png` / `ui/public/portrait.png` |

面向 **16GB 统一内存**：默认懒加载（不在启动时同时载入全部模型）。

## 启动

```bash
# 语音管线 + 角色窗（同一地址，便于麦克风授权）
bash scripts/start-pipeline.sh

# 浏览器打开：
open http://127.0.0.1:8765/
# 或半屏窗：
bash scripts/open-companion.sh half
```

## macOS 语音识别（可选，更准）

默认已切到 `stt.backend: mac-speech`（系统 Speech 框架）。

第一次需要授权，请在 **终端.app**（不要用 Cursor）运行：

```bash
bash /Users/zeq/newCompanion/scripts/grant-mac-speech.sh
```

弹出「语音识别」时点允许。若没弹窗：系统设置 → 隐私与安全性 → 语音识别 → 勾选 **MacSTT**。

然后重启管线：`bash scripts/start-pipeline.sh`

若要切回 Whisper：`config.yaml` 里把 `stt.backend` 改成 `mlx-whisper`。


## 换立绘

替换这两个文件为同名 PNG（建议竖图 3:4）：

- `assets/character/portrait.png`
- `ui/public/portrait.png`

## 目录

```
newCompanion/
├── assets/character/   # 立绘
├── models/             # 本地模型权重
├── pipeline/           # FastAPI + WebSocket 管线
├── scripts/            # 启动脚本
├── tools/llama.cpp/    # llama-server / llama-cli
└── ui/                 # 角色窗
```
