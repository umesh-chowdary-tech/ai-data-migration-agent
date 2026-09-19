import { useEffect, useRef, useState } from "react";
import { Bot, BotOff, CheckCircle2, CircleDashed, Loader2, RefreshCw, XCircle } from "lucide-react";
import type { AIStatus, Run } from "../api";
import { api, fmtTime } from "../api";
import { Button, cx } from "./ui";

/** Header badge: which AI is answering right now, with a details panel and a re-check button. */
export function AIStatusBadge({ status, onChange }: { status: AIStatus | null; onChange: (s: AIStatus) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  async function recheck() {
    setBusy(true);
    try { onChange(await api.aiCheck()); } finally { setBusy(false); }
  }

  if (!status) {
    return <span className="flex items-center gap-1.5 text-xs text-slate-400"><Loader2 className="h-3.5 w-3.5 animate-spin" />Checking AI...</span>;
  }
  const style = {
    ai: "bg-violet-50 text-violet-700 ring-violet-200",
    fallback: "bg-amber-50 text-amber-800 ring-amber-300",
    none: "bg-rose-50 text-rose-700 ring-rose-300",
  }[status.mode];
  const text = status.mode === "none" ? "No AI involved" : status.mode === "fallback" ? `AI (fallback): ${status.label}` : `AI: ${status.label}`;

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)} title="AI provider status"
        className={cx("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", style)}>
        {status.mode === "none" ? <BotOff className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
        <span className="max-w-[260px] truncate">{text}</span>
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-[380px] rounded-xl bg-white p-4 shadow-xl ring-1 ring-slate-200">
          <div className="flex items-center justify-between">
            <div className="font-semibold text-slate-900">AI providers</div>
            <Button size="sm" busy={busy} onClick={recheck}><RefreshCw className="h-3.5 w-3.5" />Re-check now</Button>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Tried in this order for every AI call; if one fails the next answers. If all fail, the agent keeps working on
            deterministic rules only.
          </p>
          <ol className="mt-3 space-y-2">
            {status.providers.length === 0 && <li className="text-sm text-slate-500">AI is switched off (LLM_PROVIDERS=none).</li>}
            {status.providers.map((p, i) => (
              <li key={p.name} className="rounded-lg bg-slate-50 px-3 py-2">
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-xs text-slate-400">{i + 1}.</span>
                  {p.status === "ok" && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
                  {p.status === "failed" && <XCircle className="h-4 w-4 text-rose-500" />}
                  {(p.status === "untested" || p.status === "not_configured") && <CircleDashed className="h-4 w-4 text-slate-400" />}
                  <span className="font-medium text-slate-800">{p.label}</span>
                  <span className="truncate font-mono text-[11px] text-slate-500">{p.model}</span>
                  {status.active === p.name && <span className="ml-auto rounded bg-violet-100 px-1.5 text-[10px] font-semibold text-violet-700">IN USE</span>}
                </div>
                <div className="mt-0.5 pl-9 text-xs">
                  {p.status === "ok" && <span className="text-emerald-700">working{p.calls ? ` · ${p.calls} call(s) answered` : ""}</span>}
                  {p.status === "failed" && <span className="text-rose-700">{p.error}{p.retry_in ? ` · will retry in ${p.retry_in}s` : ""}</span>}
                  {p.status === "not_configured" && <span className="text-slate-500">{p.error}</span>}
                  {p.status === "untested" && <span className="text-slate-500">not checked yet</span>}
                  {p.checked_at && <span className="text-slate-400"> · checked {fmtTime(p.checked_at)}</span>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

/** Full-width warning shown whenever no AI provider is answering. */
export function NoAIBanner({ status, onChange }: { status: AIStatus | null; onChange: (s: AIStatus) => void }) {
  const [busy, setBusy] = useState(false);
  if (!status || status.mode === "ai") return null;
  if (status.mode === "fallback") {
    return (
      <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-5 py-1.5 text-xs text-amber-900">
        <Bot className="h-3.5 w-3.5 shrink-0" />
        <span><span className="font-semibold">Backup AI in use:</span> {status.label}. {status.reason}</span>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-rose-200 bg-rose-50 px-5 py-2.5 text-sm text-rose-900">
      <BotOff className="h-5 w-5 shrink-0 text-rose-600" />
      <div className="min-w-0 flex-1">
        <span className="font-semibold">No AI is involved right now.</span>{" "}
        {status.providers.length ? "Every AI provider failed" : "AI is switched off"}
        {status.reason ? ` - ${status.reason}.` : "."}{" "}
        The agent keeps working on deterministic rules only (header + value evidence, parsers, validation). You won't
        see AI proposals or suggestions, and more cases may come to you for a decision.
      </div>
      <Button size="sm" busy={busy} onClick={async () => { setBusy(true); try { onChange(await api.aiCheck()); } finally { setBusy(false); } }}>
        <RefreshCw className="h-3.5 w-3.5" />Re-check AI
      </Button>
    </div>
  );
}

/** Per-run record: was AI involved in this migration, and which one answered. */
export function RunAIChip({ run }: { run: Run }) {
  const ai = run.ai;
  if (!ai) return null;
  const used = Object.entries(ai.used);
  const fresh = ai.failures.length;
  if (!used.length) {
    const pending = run.status === "running" || run.status === "queued";
    if (pending && ai.mode_at_start !== "none") return null;
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-200"
        title={ai.reason_at_start ?? ai.failures.map((f) => `${f.provider}: ${f.error}`).join("\n")}>
        <BotOff className="h-3 w-3" />No AI involved in this run
      </span>
    );
  }
  const backup = ai.mode_at_start === "fallback";
  const why = [ai.reason_at_start && backup ? `At start: ${ai.reason_at_start}` : "",
    ...ai.failures.map((f) => `${f.provider} failed during ${f.purpose}: ${f.error}`)].filter(Boolean).join("\n");
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
      fresh || backup ? "bg-amber-50 text-amber-800 ring-amber-200" : "bg-violet-50 text-violet-700 ring-violet-200")}
      title={why || undefined}>
      <Bot className="h-3 w-3" />
      {backup ? "Backup AI used" : "AI used"}: {used.map(([k, n]) => `${k} (${n}×)`).join(", ")}
      {fresh ? ` · ${fresh} provider failure(s), fell back` : ""}
      {ai.no_ai_calls ? ` · ${ai.no_ai_calls} step(s) without AI` : ""}
    </span>
  );
}
