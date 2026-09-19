import { Check, Loader2, PauseCircle } from "lucide-react";
import type { Run } from "../api";
import { RunAIChip } from "./AIStatus";
import { Badge, Card, RUN_STATUS, cx } from "./ui";

const STAGES = [
  { id: "ingest", label: "Read files" },
  { id: "map", label: "Understand columns" },
  { id: "clean", label: "Clean values" },
  { id: "reconcile", label: "Combine & de-duplicate" },
  { id: "validate", label: "Validate" },
  { id: "push", label: "Send to new system" },
  { id: "done", label: "Done" },
];

export function RunHeader({ run }: { run: Run }) {
  const idx = STAGES.findIndex((s) => s.id === run.stage);
  const paused = run.status === "waiting_for_input";
  const working = run.status === "running" || run.status === "queued";
  const st = RUN_STATUS[run.status] ?? { label: run.status, tone: "slate" as const };
  const s = run.summary;
  const inTarget = s.created + s.updated + s.unchanged;

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-slate-900">Run #{run.id}</h2>
            <Badge tone={st.tone}>
              {working && <Loader2 className="h-3 w-3 animate-spin" />}
              {paused && <PauseCircle className="h-3 w-3" />}
              {st.label}
            </Badge>
            <RunAIChip run={run} />
          </div>
          <div className="mt-0.5 text-sm text-slate-500">
            {run.label} · {run.files.join(", ")}
          </div>
        </div>
      </div>

      <ol className="mt-4 grid grid-cols-7 gap-1">
        {STAGES.map((stage, i) => {
          const done = i < idx || run.stage === "done";
          const current = i === idx && run.stage !== "done";
          return (
            <li key={stage.id} className="flex flex-col gap-1.5">
              <div className={cx("h-1.5 rounded-full",
                done ? "bg-emerald-500" : current ? (paused ? "bg-amber-400" : "bg-indigo-500 animate-pulse") : "bg-slate-200")} />
              <div className={cx("flex items-center gap-1 text-[11px] font-medium leading-tight",
                done ? "text-emerald-700" : current ? (paused ? "text-amber-700" : "text-indigo-700") : "text-slate-400")}>
                {done && <Check className="h-3 w-3 shrink-0" />}
                {stage.label}
              </div>
            </li>
          );
        })}
      </ol>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Employees found" value={s.records} hint={s.merged ? `${s.merged} duplicate merged` : undefined} />
        <Stat label="In the new system" value={inTarget} tone="emerald"
          hint={`${s.created} created · ${s.updated} updated · ${s.unchanged} unchanged`} />
        <Stat label="Needs your decision" value={s.escalations_open} tone={s.escalations_open ? "amber" : "slate"}
          hint={`${s.escalations_total - s.escalations_open} of ${s.escalations_total} resolved`} />
        <Stat label="Handled automatically" value={s.auto_fixes + s.auto_decisions} tone="violet"
          hint="fixes & decisions it made alone" />
        <Stat label="Failed / skipped" value={`${s.failed} / ${s.skipped}`} tone={s.failed ? "rose" : "slate"}
          hint={s.rolled_back ? `${s.rolled_back} rolled back` : "push failures / not migrated"} />
      </div>
      {run.error && (
        <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-rose-50 p-3 text-xs text-rose-800">{run.error}</pre>
      )}
    </Card>
  );
}

function Stat({ label, value, hint, tone = "slate" }: { label: string; value: number | string; hint?: string; tone?: string }) {
  const color = {
    slate: "text-slate-900", emerald: "text-emerald-600", amber: "text-amber-600", violet: "text-violet-600", rose: "text-rose-600",
  }[tone];
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2.5 ring-1 ring-slate-100">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className={cx("mt-0.5 text-2xl font-semibold tabular-nums", color)}>{value}</div>
      {hint && <div className="truncate text-[11px] text-slate-400" title={hint}>{hint}</div>}
    </div>
  );
}
