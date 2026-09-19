import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, Hand, Info, Sparkles, UserCheck, XCircle,
} from "lucide-react";
import type { ActivityEvent } from "../api";
import { fmtTime } from "../api";
import { cx } from "./ui";

const LEVEL = {
  info: { icon: Info, cls: "text-slate-400", label: "Info" },
  auto: { icon: Sparkles, cls: "text-violet-500", label: "Handled automatically" },
  escalation: { icon: Hand, cls: "text-amber-500", label: "Asked you" },
  human: { icon: UserCheck, cls: "text-indigo-500", label: "Your decisions" },
  success: { icon: CheckCircle2, cls: "text-emerald-500", label: "Success" },
  warning: { icon: AlertTriangle, cls: "text-orange-500", label: "Warning" },
  error: { icon: XCircle, cls: "text-rose-500", label: "Error" },
} as const;

const FILTERS = [
  { id: "all", label: "All" },
  { id: "auto", label: "Auto-handled" },
  { id: "escalation", label: "Asked you" },
  { id: "push", label: "Push" },
];

const STAGE_NAMES: Record<string, string> = {
  ingest: "Read files", map: "Understand columns", clean: "Clean", reconcile: "Combine", validate: "Validate",
  push: "Push", done: "Done", review: "Your decisions", rollback: "Rollback", error: "Error",
};

export function ActivityFeed({ events, live }: { events: ActivityEvent[]; live: boolean }) {
  const [filter, setFilter] = useState("all");
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => events.filter((e) => {
    if (filter === "all") return true;
    if (filter === "push") return e.stage === "push" || e.stage === "rollback";
    if (filter === "escalation") return e.level === "escalation" || e.level === "human";
    return e.level === filter;
  }), [events, filter]);

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown.length, follow]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-800">Live activity</span>
          {live && (
            <span className="flex items-center gap-1 text-xs text-emerald-600">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              live
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button key={f.id} onClick={() => setFilter(f.id)}
              className={cx("rounded-md px-2 py-1 text-xs font-medium", filter === f.id ? "bg-slate-800 text-white" : "text-slate-500 hover:bg-slate-100")}>
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <div ref={box} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 py-2"
        onWheel={(e) => { if (e.deltaY < 0) setFollow(false); }}
        onScroll={(e) => {
          // only a user scrolling back to the bottom re-enables follow; programmatic scrolls never disable it
          const el = e.currentTarget;
          if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) setFollow(true);
        }}>
        {shown.length === 0 && <div className="p-6 text-center text-sm text-slate-400">Nothing yet.</div>}
        {shown.map((e, i) => {
          const L = LEVEL[e.level] ?? LEVEL.info;
          const Icon = L.icon;
          const newStage = i === 0 || shown[i - 1].stage !== e.stage;
          return (
            <div key={e.id}>
              {newStage && (
                <div className="mt-2 mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  {STAGE_NAMES[e.stage] ?? e.stage}
                </div>
              )}
              <div className={cx("animate-slide-in flex gap-2 rounded-lg px-2 py-1.5 text-[13px] leading-snug",
                e.level === "escalation" && "bg-amber-50", e.level === "human" && "bg-indigo-50", e.level === "error" && "bg-rose-50")}
                title={e.data?.reason ? `Why: ${e.data.reason}` : undefined}>
                <Icon className={cx("mt-0.5 h-4 w-4 shrink-0", L.cls)} />
                <div className="min-w-0 flex-1">
                  <div className="break-words text-slate-700">{e.message}</div>
                  {e.data?.reason && e.level !== "escalation" && (
                    <div className="mt-0.5 truncate text-xs text-slate-400">{e.data.reason}</div>
                  )}
                </div>
                <span className="shrink-0 pt-0.5 font-mono text-[10px] text-slate-400">{fmtTime(e.ts)}</span>
              </div>
            </div>
          );
        })}
      </div>
      {!follow && (
        <button onClick={() => setFollow(true)} className="border-t border-slate-200 py-1.5 text-xs font-medium text-indigo-600 hover:bg-slate-50">
          Jump to latest
        </button>
      )}
    </div>
  );
}
