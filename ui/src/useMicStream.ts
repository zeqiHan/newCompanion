import { useCallback, useRef } from "react";

/** Downsample Float32 mono to PCM16 @ 16kHz. */
function downsampleTo16k(input: Float32Array, inputRate: number): Int16Array {
  if (inputRate === 16000) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }
  const ratio = inputRate / 16000;
  const newLen = Math.floor(input.length / ratio);
  const out = new Int16Array(newLen);
  for (let i = 0; i < newLen; i++) {
    const idx = Math.floor(i * ratio);
    const s = Math.max(-1, Math.min(1, input[idx] ?? 0));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function pcm16ToBase64(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

type MicHandlers = {
  onChunk: (b64: string) => void;
  onError?: (message: string) => void;
};

export function useMicStream() {
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const activeRef = useRef(false);

  const stop = useCallback(() => {
    activeRef.current = false;
    try {
      processorRef.current?.disconnect();
      sourceRef.current?.disconnect();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      void ctxRef.current?.close();
    } catch {
      /* ignore */
    }
    processorRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    ctxRef.current = null;
  }, []);

  const start = useCallback(
    async ({ onChunk, onError }: MicHandlers) => {
      stop();
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        const ctx = new AudioContext();
        const source = ctx.createMediaStreamSource(stream);
        // ScriptProcessor is deprecated but widely available; 4096 keeps CPU light.
        const processor = ctx.createScriptProcessor(4096, 1, 1);
        const mute = ctx.createGain();
        mute.gain.value = 0;

        processor.onaudioprocess = (ev) => {
          if (!activeRef.current) return;
          const input = ev.inputBuffer.getChannelData(0);
          const pcm = downsampleTo16k(input, ctx.sampleRate);
          if (pcm.length) onChunk(pcm16ToBase64(pcm));
        };

        source.connect(processor);
        processor.connect(mute);
        mute.connect(ctx.destination);

        streamRef.current = stream;
        ctxRef.current = ctx;
        sourceRef.current = source;
        processorRef.current = processor;
        activeRef.current = true;

        if (ctx.state === "suspended") await ctx.resume();
        return true;
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "无法打开麦克风（请检查系统权限）";
        onError?.(message);
        stop();
        return false;
      }
    },
    [stop],
  );

  return { start, stop };
}
