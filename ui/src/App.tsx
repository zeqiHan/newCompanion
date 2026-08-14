import { useEffect, useMemo, useRef, useState } from "react";
import { MemoryPanel } from "./MemoryPanel";
import { useCompanionSocket } from "./useCompanionSocket";
import { useMicStream } from "./useMicStream";

type Layout = "half" | "quarter";

const STATE_LABEL: Record<string, string> = {
  idle: "待命",
  listening: "聆听中",
  thinking: "思索中",
  speaking: "回应中",
};

export default function App() {
  const [layout, setLayout] = useState<Layout>(() => {
    const saved = localStorage.getItem("nc-layout");
    return saved === "quarter" || saved === "half" ? saved : "half";
  });
  const [draft, setDraft] = useState("");
  const [micError, setMicError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const {
    status,
    state,
    personaName,
    line,
    sendText,
    sendAudio,
    setListening,
    connected,
    micOn,
    stopAudio,
    memory,
  } = useCompanionSocket();
  const mic = useMicStream();

  useEffect(() => {
    localStorage.setItem("nc-layout", layout);
    document.documentElement.dataset.layout = layout;
    window.parent?.postMessage?.({ type: "newcompanion:layout", layout }, "*");
  }, [layout]);

  useEffect(() => () => mic.stop(), [mic]);

  const orbClass = useMemo(() => {
    if (state === "listening") return "orb listening";
    if (state === "thinking") return "orb thinking";
    if (state === "speaking") return "orb speaking";
    return "orb";
  }, [state]);

  async function toggleListen() {
    if (micOn) {
      mic.stop();
      setListening(false);
      setMicError("");
      return;
    }
    if (!connected) {
      setMicError("请先启动管线（scripts/start-pipeline.sh）");
      return;
    }
    const ok = await mic.start({
      onChunk: sendAudio,
      onError: (message) => setMicError(message),
    });
    if (ok) {
      setListening(true);
      setMicError("");
      stopAudio();
    }
  }

  function submit() {
    const text = draft.trim();
    if (!text) return;
    sendText(text);
    setDraft("");
    inputRef.current?.focus();
  }

  return (
    <div className={`app layout-${layout}`}>
      <header className="chrome">
        <div className="brand">
          {personaName || "小遥"}
          <span>newCompanion · local</span>
        </div>
        <div className="controls">
          <button
            type="button"
            className={`btn ${layout === "half" ? "active" : ""}`}
            onClick={() => setLayout("half")}
          >
            半屏
          </button>
          <button
            type="button"
            className={`btn ${layout === "quarter" ? "active" : ""}`}
            onClick={() => setLayout("quarter")}
          >
            右下角
          </button>
          <button
            type="button"
            className={`btn ${micOn ? "active" : ""} primary`}
            onClick={() => void toggleListen()}
          >
            {micOn ? "停止聆听" : "开始聆听"}
          </button>
        </div>
      </header>

      <main className="stage">
        <MemoryPanel memory={memory} />
        <div className="portrait" aria-hidden="true" />
        <div className="orb-wrap">
          <div className={orbClass} />
        </div>
        <div className="caption">
          <div className="state">{STATE_LABEL[state] ?? state}</div>
          <div className="line">{line || "……"}</div>
        </div>
      </main>

      <footer className="dock">
        <div className="row">
          <input
            ref={inputRef}
            value={draft}
            placeholder="也可以打字；点「开始聆听」后直接说话"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          <button type="button" className="btn primary" onClick={submit}>
            发送
          </button>
        </div>
        <div className="status">
          <span>
            <i className={`dot ${connected ? "on" : ""}`} />
            {connected ? (micOn ? "麦克风开 · 自动断句" : "已连接管线") : "未连接"}
          </span>
          <span>{micError || status}</span>
        </div>
      </footer>
    </div>
  );
}
