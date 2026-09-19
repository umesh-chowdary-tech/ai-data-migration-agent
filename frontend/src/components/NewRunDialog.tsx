import { useEffect, useState } from "react";
import { FileSpreadsheet, Upload, X } from "lucide-react";
import { api } from "../api";
import { Button, cx } from "./ui";

export function NewRunDialog({ onClose, onStarted }: { onClose: () => void; onStarted: (id: number) => void }) {
  const [samples, setSamples] = useState<{ id: string; label: string; files: string[] }[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.samples().then(setSamples); }, []);

  async function start(kind: string) {
    setBusy(kind);
    setErr(null);
    try {
      const { id } = kind === "upload" ? await api.startUpload(files) : await api.startSample(kind);
      onStarted(id);
    } catch (e: any) {
      setErr(e.message);
      setBusy(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <div className="text-lg font-semibold text-slate-900">Start a migration</div>
            <div className="text-sm text-slate-500">Give the agent the client's raw exports - it works out the rest.</div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"><X className="h-5 w-5" /></button>
        </div>
        <div className="space-y-3 px-5 py-4">
          {samples.map((s) => (
            <button key={s.id} disabled={!!busy} onClick={() => start(s.id)}
              className="w-full rounded-xl p-3 text-left ring-1 ring-slate-200 transition hover:bg-indigo-50/50 hover:ring-indigo-300 disabled:opacity-60">
              <div className="flex items-center justify-between">
                <span className="font-medium text-slate-800">{s.label}</span>
                {busy === s.id && <span className="text-xs text-indigo-600">starting...</span>}
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {s.files.map((f) => <span key={f} className="flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600"><FileSpreadsheet className="h-3 w-3" />{f}</span>)}
              </div>
            </button>
          ))}
          <div className="relative py-1 text-center text-xs text-slate-400"><span className="bg-white px-2">or upload your own CSV / Excel files</span></div>
          <label className={cx("flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed px-4 py-5 text-sm",
            files.length ? "border-indigo-300 bg-indigo-50/40" : "border-slate-200 hover:border-slate-300")}>
            <Upload className="h-5 w-5 text-slate-400" />
            {files.length ? <span className="text-slate-700">{files.map((f) => f.name).join(", ")}</span> : <span className="text-slate-500">Choose files (several files for the same entity)</span>}
            <input type="file" multiple accept=".csv,.xlsx,.xls" className="hidden" onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          </label>
          {err && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{err}</div>}
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!files.length} busy={busy === "upload"} onClick={() => start("upload")}>Start with uploaded files</Button>
        </div>
      </div>
    </div>
  );
}
