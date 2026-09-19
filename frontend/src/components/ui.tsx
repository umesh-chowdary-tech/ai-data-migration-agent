import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Tone = "slate" | "indigo" | "amber" | "emerald" | "rose" | "violet" | "sky" | "orange";
const TONES: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  rose: "bg-rose-50 text-rose-700 ring-rose-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
  sky: "bg-sky-50 text-sky-700 ring-sky-200",
  orange: "bg-orange-50 text-orange-700 ring-orange-200",
};

export function Badge({ tone = "slate", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Button({
  children, onClick, variant = "secondary", size = "md", disabled, busy, className, title, type = "button",
}: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "ghost" | "danger" | "success";
  size?: "sm" | "md"; disabled?: boolean; busy?: boolean; className?: string; title?: string; type?: "button" | "submit";
}) {
  const v = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-500 shadow-sm",
    success: "bg-emerald-600 text-white hover:bg-emerald-500 shadow-sm",
    secondary: "bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 shadow-sm",
    ghost: "text-slate-600 hover:bg-slate-100",
    danger: "bg-white text-rose-700 ring-1 ring-inset ring-rose-300 hover:bg-rose-50",
  }[variant];
  const s = size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm";
  return (
    <button type={type} title={title} disabled={disabled || busy} onClick={onClick}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:opacity-50 disabled:cursor-not-allowed", v, s, className)}>
      {busy && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-xl bg-white ring-1 ring-slate-200 shadow-sm", className)}>{children}</div>;
}

export function ConfidenceBar({ value, className }: { value: number; className?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const color = pct >= 80 ? "bg-emerald-500" : pct >= 60 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className={cx("flex items-center gap-2", className)}>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-200">
        <div className={cx("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-slate-500">{pct}%</span>
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-14 text-center">
      {icon && <div className="text-slate-300">{icon}</div>}
      <div className="font-medium text-slate-700">{title}</div>
      {children && <div className="max-w-md text-sm text-slate-500">{children}</div>}
    </div>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[12px] text-slate-700">{children}</span>;
}

export const RECORD_STATUS: Record<string, { label: string; tone: Tone }> = {
  pending: { label: "Pending", tone: "slate" },
  ready: { label: "Ready", tone: "sky" },
  needs_review: { label: "Needs decision", tone: "amber" },
  pushed: { label: "In target", tone: "emerald" },
  unchanged: { label: "Unchanged", tone: "slate" },
  waiting: { label: "Waiting on manager", tone: "sky" },
  failed: { label: "Push failed", tone: "rose" },
  skipped: { label: "Skipped", tone: "slate" },
  merged: { label: "Merged duplicate", tone: "violet" },
  rolled_back: { label: "Rolled back", tone: "orange" },
};

export const RUN_STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "slate" },
  running: { label: "Agent working", tone: "indigo" },
  waiting_for_input: { label: "Paused - needs your decision", tone: "amber" },
  needs_review: { label: "Migrated - items need your decision", tone: "amber" },
  completed: { label: "Completed", tone: "emerald" },
  attention: { label: "Some pushes failed", tone: "rose" },
  failed: { label: "Agent error", tone: "rose" },
  rolled_back: { label: "Rolled back", tone: "orange" },
};
