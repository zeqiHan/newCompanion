type MemModel = {
  id: string;
  label: string;
  model?: string;
  disk_mb?: number;
  est_ram_mb?: number;
  loaded?: boolean;
};

export type MemoryInfo = {
  process?: { rss_mb?: number };
  system?: { used_gb?: number; total_gb?: number; percent?: number };
  estimate?: { loaded_models_mb?: number };
  models?: MemModel[];
};

export function MemoryPanel({ memory }: { memory: MemoryInfo | null }) {
  if (!memory) {
    return (
      <aside className="mem">
        <h3>Memory</h3>
        <div className="sys">读取中…</div>
      </aside>
    );
  }
  const sys = memory.system || {};
  const proc = memory.process || {};
  const est = memory.estimate || {};
  const pct = Math.max(0, Math.min(100, Number(sys.percent) || 0));

  return (
    <aside className="mem" aria-label="memory">
      <h3>Memory</h3>
      <div className="sys">
        系统 {sys.used_gb ?? "—"} / {sys.total_gb ?? "—"} GB
        <br />
        管线 RSS <b>{proc.rss_mb ?? "—"} MB</b>
        {est.loaded_models_mb != null ? ` · 模型估 ${est.loaded_models_mb} MB` : ""}
      </div>
      <div className="bar">
        <i style={{ width: `${pct}%` }} />
      </div>
      {(memory.models || []).map((m) => (
        <div key={m.id}>
          <div className="row">
            <span>
              <span className={`tag ${m.loaded ? "on" : ""}`}>{m.loaded ? "ON" : "off"}</span>{" "}
              {m.label}
            </span>
            <b>{m.loaded ? `${m.est_ram_mb} MB` : "—"}</b>
          </div>
          <div className="row muted">
            <span>{(m.model || "").split("/").pop()}</span>
            <span>盘 {m.disk_mb} MB</span>
          </div>
        </div>
      ))}
    </aside>
  );
}
