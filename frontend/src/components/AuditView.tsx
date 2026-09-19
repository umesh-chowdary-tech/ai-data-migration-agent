import { useState } from "react";
import { Bot, Download, User } from "lucide-react";
import type { AuditEntry } from "../api";
import { fmtTime, fmtValue } from "../api";
import { Button, Card, Empty, cx } from "./ui";

export function AuditView({ entries, runId }: { entries: AuditEntry[]; runId: number }) {
  const [who, setWho] = useState<"all" | "agent" | "human">("all");
  const [q, setQ] = useState("");
  const shown = entries.filter((e) =>
    (who === "all" || (who === "agent" ? e.actor === "agent" : e.actor !== "agent")) &&
    (!q || `${e.action} ${e.entity} ${e.reason}`.toLowerCase().includes(q.toLowerCase())));

  function exportCsv() {
    // Server-side export: client data in the trail is neutralised so it can't run as an Excel formula.
    const a = document.createElement("a");
    a.href = `/api/runs/${runId}/audit.csv`;
    a.click();
  }

  if (!entries.length) return <Empty title="No audit entries yet" />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {(["all", "agent", "human"] as const).map((w) => (
          <button key={w} onClick={() => setWho(w)}
            className={cx("rounded-md px-2 py-1 text-xs font-medium", who === w ? "bg-slate-800 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200")}>
            {w === "all" ? `Everything (${entries.length})` : w === "agent" ? "Agent actions" : "Human decisions"}
          </button>
        ))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter..." className="w-48 rounded-lg border-0 px-2.5 py-1 text-sm ring-1 ring-slate-200" />
        <Button size="sm" className="ml-auto" onClick={exportCsv}><Download className="h-3.5 w-3.5" />Export CSV</Button>
      </div>
      <Card className="overflow-hidden">
        <table className="w-full text-[13px]">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr><th className="px-3 py-2 font-medium">Time</th><th className="px-2 py-2 font-medium">Who</th><th className="px-2 py-2 font-medium">Action</th><th className="px-2 py-2 font-medium">On</th><th className="px-2 py-2 font-medium">Result / why</th></tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={e.id} className="border-t border-slate-100 align-top">
                <td className="px-3 py-1.5 font-mono text-[11px] whitespace-nowrap text-slate-400">{fmtTime(e.ts)}</td>
                <td className="px-2 py-1.5 whitespace-nowrap">
                  {e.actor === "agent"
                    ? <span className="flex items-center gap-1 text-violet-700"><Bot className="h-3.5 w-3.5" />Agent</span>
                    : <span className="flex items-center gap-1 text-indigo-700"><User className="h-3.5 w-3.5" />{e.actor}</span>}
                </td>
                <td className="px-2 py-1.5 font-medium whitespace-nowrap text-slate-700">{e.action}</td>
                <td className="max-w-[180px] truncate px-2 py-1.5 font-mono text-[11px] text-slate-600" title={e.entity}>{e.entity}</td>
                <td className="px-2 py-1.5 text-slate-600">
                  {e.after !== null && e.after !== undefined && typeof e.after !== "object" && <span className="mr-1 font-mono text-[12px] text-emerald-700">{fmtValue(e.after)}</span>}
                  {e.after && typeof e.after === "object" && !Array.isArray(e.after) && (e.action.startsWith("push") || e.action === "rolled_back"
                    ? <span className="mr-1 text-xs text-slate-400">{Object.keys(e.after).length} fields</span>
                    : <span className="mr-1 font-mono text-[12px] text-emerald-700">
                        {Object.entries(e.after).filter(([k]) => k !== "question").map(([k, v]) => `${k} = ${fmtValue(v)}`).join(", ")}
                      </span>)}
                  <span>{e.reason}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
