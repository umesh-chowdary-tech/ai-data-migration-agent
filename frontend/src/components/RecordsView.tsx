import { useMemo, useState } from "react";
import { RotateCcw, RefreshCw, Search, Undo2, X } from "lucide-react";
import type { RecordRow, Run, SchemaField } from "../api";
import { api, fmtTime, fmtValue } from "../api";
import { Badge, Button, Card, Empty, RECORD_STATUS, cx } from "./ui";

export function RecordsView({ run, records, schema, actor, onChanged }: {
  run: Run; records: RecordRow[]; schema: SchemaField[]; actor: string; onChanged: () => void;
}) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<string>("all");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const counts = useMemo(() => records.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {}), [records]);
  const shown = records.filter((r) =>
    (status === "all" || r.status === status) &&
    (!q || `${r.key} ${r.name} ${r.data.email ?? ""} ${r.data.department ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  const failed = counts.failed ?? 0;
  const pushedHere = (counts.pushed ?? 0);
  const open = records.find((r) => r.key === openKey) ?? null;

  async function act(kind: string, fn: () => Promise<unknown>) {
    setBusy(kind);
    try { await fn(); onChanged(); } finally { setTimeout(() => setBusy(null), 600); }
  }

  if (!records.length) return <Empty title="No records yet">Records appear once the agent has combined the files.</Empty>;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute top-2 left-2.5 h-4 w-4 text-slate-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, ID, email..."
            className="w-56 rounded-lg border-0 py-1.5 pr-3 pl-8 text-sm ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500" />
        </div>
        <div className="flex flex-wrap gap-1">
          {["all", ...Object.keys(RECORD_STATUS).filter((s) => counts[s])].map((s) => (
            <button key={s} onClick={() => setStatus(s)}
              className={cx("rounded-md px-2 py-1 text-xs font-medium", status === s ? "bg-slate-800 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50")}>
              {s === "all" ? `All ${records.length}` : `${RECORD_STATUS[s].label} ${counts[s]}`}
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          {failed > 0 && (
            <Button size="sm" variant="primary" busy={busy === "retry"} onClick={() => act("retry", () => api.retry(run.id, null, actor))}>
              <RefreshCw className="h-3.5 w-3.5" />Retry {failed} failed
            </Button>
          )}
          {pushedHere > 0 && (
            <Button size="sm" variant="danger" busy={busy === "rollback"}
              onClick={() => confirm(`Roll back all ${pushedHere} records this run pushed? Created records are deleted and updated records restored to their previous version.`) && act("rollback", () => api.rollback(run.id, null, actor))}>
              <Undo2 className="h-3.5 w-3.5" />Roll back this run
            </Button>
          )}
        </div>
      </div>

      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2 font-medium">ID</th><th className="px-2 py-2 font-medium">Name</th>
              <th className="px-2 py-2 font-medium">Email</th><th className="px-2 py-2 font-medium">Department</th>
              <th className="px-2 py-2 font-medium">Sources</th><th className="px-2 py-2 font-medium">Status</th>
              <th className="px-2 py-2 font-medium">Changes</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const st = RECORD_STATUS[r.status] ?? { label: r.status, tone: "slate" as const };
              const files = [...new Set(r.rows.map((x) => x.file.split("_")[0]))];
              return (
                <tr key={r.key} onClick={() => setOpenKey(r.key)} className="cursor-pointer border-t border-slate-100 hover:bg-indigo-50/40">
                  <td className="px-3 py-1.5 font-mono text-xs text-slate-600">{r.key}</td>
                  <td className="px-2 py-1.5 font-medium text-slate-800">{r.name}</td>
                  <td className="max-w-[220px] truncate px-2 py-1.5 text-slate-600">{r.data.email ?? "—"}</td>
                  <td className="px-2 py-1.5 text-slate-600">{r.data.department ?? "—"}</td>
                  <td className="px-2 py-1.5"><div className="flex gap-1">{files.map((f) => <Badge key={f}>{f}</Badge>)}</div></td>
                  <td className="px-2 py-1.5">
                    <Badge tone={st.tone}>{st.label}{r.push_op && r.status === "pushed" ? ` (${r.push_op === "create" ? "created" : "updated"})` : ""}</Badge>
                  </td>
                  <td className="px-2 py-1.5 text-xs text-slate-500">{r.changes.length}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      {open && (
        <RecordDrawer r={open} schema={schema} onClose={() => setOpenKey(null)}
          onRetry={() => act("retry1", () => api.retry(run.id, [open.key], actor))}
          onRollback={() => confirm(`Roll back ${open.key} in the target?`) && act("rb1", () => api.rollback(run.id, [open.key], actor))} />
      )}
    </div>
  );
}

function RecordDrawer({ r, schema, onClose, onRetry, onRollback }: {
  r: RecordRow; schema: SchemaField[]; onClose: () => void; onRetry: () => void; onRollback: () => void;
}) {
  const st = RECORD_STATUS[r.status] ?? { label: r.status, tone: "slate" as const };
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/20" onClick={onClose}>
      <div className="scroll-thin h-full w-full max-w-xl overflow-y-auto bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-start justify-between border-b border-slate-200 bg-white px-5 py-4">
          <div>
            <div className="font-mono text-xs text-slate-500">{r.key}</div>
            <div className="text-lg font-semibold text-slate-900">{r.name}</div>
            <div className="mt-1 flex items-center gap-2">
              <Badge tone={st.tone}>{st.label}</Badge>
              {r.push_attempts > 0 && <span className="text-xs text-slate-500">{r.push_attempts} push attempt(s)</span>}
            </div>
            {r.last_error && <div className="mt-1 text-xs text-rose-600">{r.last_error}</div>}
          </div>
          <div className="flex items-center gap-2">
            {(r.status === "failed" || r.status === "waiting") && <Button size="sm" onClick={onRetry}><RotateCcw className="h-3.5 w-3.5" />Retry</Button>}
            {r.status === "pushed" && <Button size="sm" variant="danger" onClick={onRollback}><Undo2 className="h-3.5 w-3.5" />Roll back</Button>}
            <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"><X className="h-5 w-5" /></button>
          </div>
        </div>

        <div className="space-y-5 px-5 py-4">
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Final values sent to the new system</h4>
            <table className="w-full text-sm">
              <tbody>
                {schema.map((f) => (
                  <tr key={f.name} className="border-t border-slate-100">
                    <td className="w-40 py-1.5 pr-2 text-slate-500">{f.label}</td>
                    <td className="py-1.5 font-mono text-[12px] text-slate-800">{fmtValue(r.data[f.name])}</td>
                    <td className="py-1.5 text-right text-[11px] text-slate-400">{r.field_sources[f.name]?.replace(/\.(csv|xlsx)/g, "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">What changed and why ({r.changes.length})</h4>
            {r.changes.length === 0 ? <div className="text-sm text-slate-400">No changes - values were already clean.</div> : (
              <ol className="space-y-1.5">
                {r.changes.map((c, i) => (
                  <li key={i} className="rounded-lg bg-slate-50 px-3 py-1.5 text-[13px]">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium text-slate-700">{c.field}</span>
                      <span className="font-mono text-[12px] text-rose-600 line-through decoration-rose-300">{fmtValue(c.from)}</span>
                      <span className="text-slate-400">→</span>
                      <span className="font-mono text-[12px] text-emerald-700">{fmtValue(c.to)}</span>
                    </div>
                    <div className="text-xs text-slate-500">
                      {c.reason} · <span className={c.by === "agent" ? "text-violet-600" : "text-indigo-600"}>{c.by === "agent" ? "agent" : c.by}</span>
                      {c.file ? ` · ${c.file}` : ""}{c.ts ? ` · ${fmtTime(c.ts)}` : ""}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Source rows ({r.rows.length})</h4>
            {r.rows.map((row, i) => (
              <details key={i} className="mb-1.5 rounded-lg ring-1 ring-slate-200">
                <summary className="cursor-pointer px-3 py-1.5 text-sm text-slate-700">{row.file} · row {row.row}</summary>
                <table className="w-full text-xs">
                  <tbody>
                    {Object.entries(row.raw).map(([k, v]) => (
                      <tr key={k} className="border-t border-slate-100"><td className="w-40 px-3 py-1 text-slate-500">{k}</td><td className="px-2 py-1 font-mono">{fmtValue(v)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </details>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
