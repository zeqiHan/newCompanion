import { useCallback, useEffect, useRef, useState } from "react";
import type { MemoryInfo } from "./MemoryPanel";

type ServerEvent = {
  type: string;
  state?: string;
  text?: string;
  role?: string;
  data?: string;
  format?: string;
  message?: string;
  persona?: { display_name?: string; name?: string };
  greeting?: string;
  mode?: string;
  mic_on?: boolean;
  speech?: boolean;
  memory?: MemoryInfo;
};

const WS_URL =
  import.meta.env.VITE_WS_URL ??
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname || "127.0.0.1"}:8765/ws`;

export function useCompanionSocket() {
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState("idle");
  const [personaName, setPersonaName] = useState("小遥");
  const [line, setLine] = useState("打开管线后，就可以开始对话。");
  const [status, setStatus] = useState("connecting…");
  const [micOn, setMicOn] = useState(false);
  const [memory, setMemory] = useState<MemoryInfo | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const queueRef = useRef<string[]>([]);
  const playingRef = useRef(false);

  const pumpQueue = useCallback(() => {
    if (playingRef.current) return;
    const next = queueRef.current.shift();
    if (!next) return;
    if (!audioRef.current) audioRef.current = new Audio();
    const audio = audioRef.current;
    playingRef.current = true;
    audio.onended = () => {
      playingRef.current = false;
      pumpQueue();
    };
    audio.onerror = () => {
      playingRef.current = false;
      pumpQueue();
    };
    audio.src = next;
    void audio.play().catch(() => {
      playingRef.current = false;
      pumpQueue();
    });
  }, []);

  const stopAudio = useCallback(() => {
    queueRef.current = [];
    playingRef.current = false;
    if (!audioRef.current) return;
    audioRef.current.pause();
    audioRef.current.currentTime = 0;
  }, []);

  const playAudio = useCallback((format: string, data: string) => {
    const mime = format === "wav" ? "audio/wav" : `audio/${format}`;
    queueRef.current.push(`data:${mime};base64,${data}`);
    pumpQueue();
  }, [pumpQueue]);

  useEffect(() => {
    let closed = false;
    let retry = 0;
    let timer: number | undefined;
    let memTimer: number | undefined;

    const pollMemory = async () => {
      try {
        const res = await fetch("http://127.0.0.1:8765/api/memory");
        if (res.ok) setMemory((await res.json()) as MemoryInfo);
      } catch {
        /* ignore */
      }
    };
    void pollMemory();
    memTimer = window.setInterval(() => void pollMemory(), 2000);

    const connect = () => {
      if (closed) return;
      setStatus("connecting…");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        setStatus("online");
      };

      ws.onclose = () => {
        setConnected(false);
        setMicOn(false);
        setStatus("offline · retrying");
        const wait = Math.min(8000, 600 * 2 ** retry);
        retry += 1;
        timer = window.setTimeout(connect, wait);
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onmessage = (ev) => {
        let msg: ServerEvent;
        try {
          msg = JSON.parse(ev.data) as ServerEvent;
        } catch {
          return;
        }

        if (msg.type === "state") {
          if (msg.state) setState(msg.state);
          if (typeof msg.mic_on === "boolean") setMicOn(msg.mic_on);
          if (msg.persona?.display_name || msg.persona?.name) {
            setPersonaName(msg.persona.display_name || msg.persona.name || "小遥");
          }
          if (msg.greeting) setLine(msg.greeting);
          if (msg.mode) setStatus(`online · ${msg.mode}`);
        }

        if (msg.type === "transcript" && msg.role === "user" && msg.text) {
          setLine(`你：${msg.text}`);
        }

        if (msg.type === "reply" && msg.text) {
          setLine(msg.text);
        }

        if (msg.type === "audio" && msg.data) {
          playAudio(msg.format || "wav", msg.data);
        }

        if (msg.type === "barge_in") {
          stopAudio();
        }

        if (msg.type === "memory" && msg.memory) {
          setMemory(msg.memory);
        }
        if (msg.type === "state" && msg.memory) {
          setMemory(msg.memory);
        }

        if (msg.type === "info" && msg.message) {
          setStatus(msg.message);
        }

        if (msg.type === "error" && msg.message) {
          setStatus(msg.message);
        }
      };
    };

    connect();

    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      if (memTimer) window.clearInterval(memTimer);
      wsRef.current?.close();
    };
  }, [playAudio, stopAudio]);

  const sendText = useCallback((text: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setLine("管线还没连上。先启动 pipeline，再发消息。");
      return;
    }
    ws.send(JSON.stringify({ type: "text", text }));
  }, []);

  const sendAudio = useCallback((b64: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "audio", data: b64 }));
  }, []);

  const setListening = useCallback((on: boolean) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setState(on ? "listening" : "idle");
      setMicOn(on);
      return false;
    }
    ws.send(JSON.stringify({ type: "listen", on }));
    setMicOn(on);
    return true;
  }, []);

  return {
    connected,
    state,
    personaName,
    line,
    status,
    micOn,
    memory,
    sendText,
    sendAudio,
    setListening,
    stopAudio,
  };
}
