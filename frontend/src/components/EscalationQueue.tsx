import { useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, PauseCircle } from "lucide-react";
import type { Escalation, Run } from "../api";
import { fmtTime, fmtValue } from "../api";
import { EscalationCard } from "./EscalationCard";
import { Badge, Empty, cx } from "./ui";

export function EscalationQueue({ run, items, actor, onChanged }: { run: Run; items: Escalation[]; actor: string; onChanged: () => void }) {
  const open = items.filter((e) => e.status === "open");
  const done = items.filter((e) => e.status !== "open").sort((a, b) => (b.resolved_at ?? 0) - (a.resolved_at ?? 0));
  const blocking = open.filter((e) => e.blocking);
  const [showDone, setShowDone] = useState(false);
  const s = run.summary;

  return (
    <div className="space-y-3">
      <div className="text-sm text-slate-500">
        I only ask when I can't prove the answer from the data. So far I handled{" "}
        <span className="font-semibold text-violet-700">{s.auto_fixes + s.auto_decisions}</span> things on my own and asked you about{" "}
        <span className="font-semibold text-amber-700">{s.escalations_total}</span>.
      </div>

      {blocking.length > 0 && (
        <div className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900 ring-1 ring-amber-200">
          <PauseCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <span className="font-semibold">The migration is paused.</span> These {blocking.length} decision(s) affect how every row of a
            column is read, so I need them before continuing. Everything else is already prepared.
          </div>
        </div>
      )}

      {open.length === 0 ? (
        <Empty icon={<CheckCircle2 className="h-10 w-10" />} title={run.status === "running" || run.status === "queued" ? "Nothing for you yet" : "Nothing needs your decision"}>
          {run.status === "running" || run.status === "queued"
            ? "The agent is working. Anything it isn't sure about will show up here."
            : "Every uncertain case has been resolved."}
        </Empty>
      ) : (
        <div className="space-y-3">
          {open.map((e) => <EscalationCard key={e.id} e={e} actor={actor} onResolved={onChanged} />)}
        </div>
      )}

      {done.length > 0 && (
        <div className="pt-2">
          <button onClick={() => setShowDone(!showDone)} className="flex items-center gap-1 text-sm font-medium text-slate-600 hover:text-slate-900">
            {showDone ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            Resolved ({done.length})
          </button>
          {showDone && (
            <div className="mt-2 divide-y divide-slate-100 rounded-xl bg-white ring-1 ring-slate-200">
              {done.map((e) => (
                <div key={e.id} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-sm">
                  <Badge tone={e.status === "approved" ? "emerald" : e.status === "corrected" ? "indigo" : e.status === "processing" ? "slate" : "rose"}>
                    {e.status === "processing" ? "applying..." : e.status}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate text-slate-700" title={e.title}>{e.title}</span>
                  <span className={cx("max-w-[40%] truncate font-mono text-xs text-slate-500")} title={fmtValue(e.resolution?.value)}>
                    {e.status === "rejected" ? e.reject_label : fmtValue(e.resolution?.value)}
                  </span>
                  <span className="text-xs text-slate-400">{e.resolved_by} · {e.resolved_at ? fmtTime(e.resolved_at) : ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
