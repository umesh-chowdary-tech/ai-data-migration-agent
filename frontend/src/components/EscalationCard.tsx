import { useState } from "react";
import { Check, ChevronDown, Lightbulb, Pencil, PauseCircle, X } from "lucide-react";
import type { Escalation, FormField } from "../api";
import { api, fmtValue } from "../api";
import { Badge, Button, ConfidenceBar, Mono, cx } from "./ui";

const TYPE_TONE: Record<string, "indigo" | "amber" | "violet" | "rose" | "sky" | "orange"> = {
  mapping: "indigo", date_format: "indigo", unknown_value: "violet", invalid_value: "orange", conflict: "sky",
  duplicate: "violet", validation: "rose", push_failure: "rose",
};

export function EscalationCard({ e, actor, onResolved }: { e: Escalation; actor: string; onResolved: () => void }) {
  const [mode, setMode] = useState<null | "correct" | "reject">(null);
  const [choice, setChoice] = useState<any>(() => (e.correct?.kind === "choice" ? e.proposal?.value ?? e.correct.options[0]?.value : undefined));
  const [fields, setFields] = useState<Record<string, any>>(() => initialFields(e));
  const [note, setNote] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(action: "approve" | "correct" | "reject") {
    setBusy(action);
    setError(null);
    try {
      const value = action !== "correct" ? undefined : e.correct?.kind === "choice" ? choice : fields;
      await api.resolve(e.id, { action, value, note, remember, actor });
      onResolved();
    } catch (err: any) {
      setError(err.message);
      setBusy(null);
    }
  }

  return (
    <div className={cx("animate-slide-in rounded-xl bg-white shadow-sm ring-1", e.blocking ? "ring-amber-300" : "ring-slate-200")}>
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 px-4 pt-3 pb-2.5">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <Badge tone={TYPE_TONE[e.type] ?? "slate"}>{e.type_label}</Badge>
            {e.blocking && <Badge tone="amber"><PauseCircle className="h-3 w-3" />Migration paused on this</Badge>}
            {e.record_keys.length > 0 && <span className="font-mono text-[11px] text-slate-400">{e.record_keys.join(", ")}</span>}
          </div>
          <h3 className="font-semibold leading-snug text-slate-900">{e.title}</h3>
          <p className="mt-0.5 text-sm text-slate-600">{e.question}</p>
        </div>
      </div>

      <div className="space-y-3 px-4 py-3">
        <div className="rounded-lg bg-slate-50 px-3 py-2 text-[13px] text-slate-600">
          <span className="font-medium text-slate-700">Why I'm asking: </span>{e.reason}
        </div>
        <Evidence e={e} />
        {e.proposal && (
          <div className="rounded-lg bg-emerald-50/60 px-3 py-2 ring-1 ring-emerald-100">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 text-sm">
                <Lightbulb className="h-4 w-4 text-emerald-600" />
                <span className="text-slate-600">My suggestion:</span>
                <span className="font-semibold text-slate-900">{e.proposal.label}</span>
              </div>
              {typeof e.proposal.confidence === "number" && <ConfidenceBar value={e.proposal.confidence} />}
            </div>
            {e.proposal.details?.map((d, i) => <div key={i} className="mt-1 pl-5.5 text-xs text-slate-500">{d}</div>)}
          </div>
        )}
      </div>

      {mode === "correct" && e.correct && (
        <div className="border-t border-slate-100 bg-slate-50/60 px-4 py-3">
          {e.correct.kind === "choice" ? (
            <ChoiceInput options={e.correct.options} more={e.correct.more} value={choice} onChange={setChoice} />
          ) : (
            <FieldsInput fields={e.correct.fields} values={fields} onChange={setFields} />
          )}
        </div>
      )}
      {mode === "reject" && (
        <div className="border-t border-slate-100 bg-rose-50/50 px-4 py-2.5 text-sm text-rose-800">
          Reject means: <span className="font-semibold">{e.reject_label}</span>. This is recorded in the audit trail.
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-3">
        <div className="flex flex-1 flex-wrap items-center gap-3">
          <input value={note} onChange={(ev) => setNote(ev.target.value)} placeholder="Note for the audit trail (optional)"
            className="min-w-[180px] flex-1 rounded-lg border-0 px-2.5 py-1.5 text-sm ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500" />
          <label className="flex items-center gap-1.5 text-xs text-slate-600" title="Next time the same situation appears, the agent applies your decision automatically">
            <input type="checkbox" checked={remember} onChange={(ev) => setRemember(ev.target.checked)} className="rounded accent-indigo-600" />
            Remember for future runs
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          {mode === null && (
            <>
              {e.proposal && <Button variant="success" busy={busy === "approve"} onClick={() => submit("approve")}><Check className="h-4 w-4" />Approve</Button>}
              {e.correct && <Button onClick={() => setMode("correct")}><Pencil className="h-4 w-4" />Correct</Button>}
              <Button variant="danger" onClick={() => setMode("reject")}><X className="h-4 w-4" />Reject</Button>
            </>
          )}
          {mode === "correct" && (
            <>
              <Button variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
              <Button variant="primary" busy={busy === "correct"} onClick={() => submit("correct")}>Save correction</Button>
            </>
          )}
          {mode === "reject" && (
            <>
              <Button variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
              <Button variant="danger" busy={busy === "reject"} onClick={() => submit("reject")}>Confirm reject</Button>
            </>
          )}
        </div>
      </div>
      {error && <div className="border-t border-rose-100 bg-rose-50 px-4 py-2 text-sm text-rose-700">{error}</div>}
    </div>
  );
}

function initialFields(e: Escalation): Record<string, any> {
  if (e.correct?.kind !== "fields") return {};
  const out: Record<string, any> = {};
  for (const f of e.correct.fields) {
    const proposed = e.proposal && typeof e.proposal.value === "object" ? e.proposal.value?.[f.name] : undefined;
    out[f.name] = proposed ?? f.suggestion ?? f.value ?? "";
  }
  return out;
}

function ChoiceInput({ options, more, value, onChange }: { options: any[]; more?: any[]; value: any; onChange: (v: any) => void }) {
  const inMore = more?.some((o) => o.value === value);
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Choose the correct answer</div>
      {options.map((o) => (
        <label key={String(o.value)} className={cx("flex cursor-pointer items-start gap-2 rounded-lg bg-white px-3 py-2 ring-1",
          value === o.value ? "ring-2 ring-indigo-500" : "ring-slate-200 hover:ring-slate-300")}>
          <input type="radio" aria-label={o.label} className="mt-1 accent-indigo-600" checked={value === o.value} onChange={() => onChange(o.value)} />
          <div className="min-w-0">
            <div className="text-sm font-medium text-slate-800">{o.label}</div>
            {o.detail && <div className="text-xs text-slate-500">{o.detail}</div>}
          </div>
        </label>
      ))}
      {more && more.length > 0 && (
        <div className="flex items-center gap-2 pt-1">
          <span className="text-xs text-slate-500">Something else:</span>
          <div className="relative">
            <select value={inMore ? value : ""} onChange={(ev) => ev.target.value && onChange(ev.target.value)}
              className={cx("appearance-none rounded-lg border-0 bg-white py-1.5 pr-8 pl-2.5 text-sm ring-1", inMore ? "ring-2 ring-indigo-500" : "ring-slate-200")}>
              <option value="">Pick another field...</option>
              {more.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <ChevronDown className="pointer-events-none absolute top-2 right-2 h-4 w-4 text-slate-400" />
          </div>
        </div>
      )}
    </div>
  );
}

function FieldsInput({ fields, values, onChange }: { fields: FormField[]; values: Record<string, any>; onChange: (v: Record<string, any>) => void }) {
  return (
    <div className="space-y-2.5">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Enter the correct values</div>
      {fields.map((f) => (
        <div key={f.name}>
          <label className="text-sm font-medium text-slate-700">
            {f.label} {f.required && <span className="text-rose-500">*</span>}
            {f.value !== null && f.value !== undefined && f.value !== "" && (
              <span className="ml-2 text-xs font-normal text-slate-400">current: <Mono>{fmtValue(f.value)}</Mono></span>
            )}
          </label>
          {f.options ? (
            <select value={values[f.name] ?? ""} onChange={(ev) => onChange({ ...values, [f.name]: ev.target.value || null })}
              className="mt-1 w-full rounded-lg border-0 bg-white px-2.5 py-1.5 text-sm ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500">
              <option value="">(empty)</option>
              {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input type={f.type === "date" ? "date" : "text"} value={values[f.name] ?? ""}
              onChange={(ev) => onChange({ ...values, [f.name]: ev.target.value })}
              placeholder={f.type === "phone" ? "e.g. 98765 43210" : f.required ? "required" : "leave empty to clear"}
              className="mt-1 w-full rounded-lg border-0 bg-white px-2.5 py-1.5 font-mono text-sm ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500" />
          )}
          {f.suggestion !== undefined && f.suggestion !== null && (
            <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
              <Lightbulb className="h-3.5 w-3.5 text-emerald-600" />
              Suggested <Mono>{fmtValue(f.suggestion)}</Mono>{f.why ? ` - ${f.why}` : ""}
              {values[f.name] !== f.suggestion && (
                <button className="font-medium text-indigo-600 hover:underline" onClick={() => onChange({ ...values, [f.name]: f.suggestion })}>use</button>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Evidence panels - one per escalation type, so the consultant can decide at a glance
// ---------------------------------------------------------------------------------------------

function Evidence({ e }: { e: Escalation }) {
  const c = e.context ?? {};
  switch (e.type) {
    case "mapping":
      return (
        <div className="space-y-2">
          <Samples label={`Values in '${c.column}' (${c.file})`} values={c.samples} />
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-slate-400"><th className="pb-1 font-medium">Could be</th><th className="pb-1 font-medium">Fit</th><th className="pb-1 font-medium">Evidence</th></tr></thead>
            <tbody>
              {c.candidates?.map((x: any) => (
                <tr key={x.field} className="border-t border-slate-100 align-top">
                  <td className="py-1.5 pr-2 font-medium text-slate-800">{x.label}</td>
                  <td className="py-1.5 pr-2"><ConfidenceBar value={x.score} /></td>
                  <td className="py-1.5 text-xs text-slate-500">{x.evidence?.join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "date_format":
      return (
        <div className="space-y-1.5">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-slate-400"><th className="pb-1 font-medium">Value in file</th><th className="pb-1 font-medium">Read day-first</th><th className="pb-1 font-medium">Read month-first</th></tr></thead>
            <tbody>
              {c.examples?.map((x: any) => (
                <tr key={x.raw} className="border-t border-slate-100"><td className="py-1"><Mono>{x.raw}</Mono></td><td className="py-1">{x.DMY}</td><td className="py-1">{x.MDY}</td></tr>
              ))}
            </tbody>
          </table>
          <div className="text-xs text-slate-500">
            Cross-check with other files: {c.evidence?.DMY ?? 0} match day-first, {c.evidence?.MDY ?? 0} match month-first (I need {c.evidence?.needed} to decide alone).
          </div>
        </div>
      );
    case "unknown_value":
      return (
        <div className="space-y-2 text-sm">
          <div>Value: <Badge tone="violet">{c.raw}</Badge> <span className="text-slate-500">allowed: {c.allowed?.join(", ")}</span></div>
          <div className="flex flex-wrap gap-1.5">
            {c.records?.map((r: any) => (
              <span key={r.key} className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                <span className="font-mono">{r.key}</span> {r.name}{r.job_title ? ` - ${r.job_title}` : ""}
              </span>
            ))}
          </div>
        </div>
      );
    case "invalid_value":
      return (
        <div className="space-y-2">
          <RecordSummary r={c.record} />
          <div className="text-sm">Value in <span className="font-medium">{c.file}</span>: <Badge tone="orange"><span className="font-mono">{c.raw}</span></Badge></div>
        </div>
      );
    case "conflict":
      return (
        <div className="space-y-2">
          <RecordSummary r={c.record} />
          <div className="grid gap-1.5 sm:grid-cols-2">
            {c.options?.map((o: any) => (
              <div key={String(o.value)} className="rounded-lg bg-sky-50 px-3 py-2 ring-1 ring-sky-100">
                <div className="text-xs text-sky-700">{o.files?.join(", ")}</div>
                <div className="font-mono text-sm font-medium text-slate-900">{fmtValue(o.value)}</div>
              </div>
            ))}
          </div>
        </div>
      );
    case "duplicate": {
      const [a, b] = c.records ?? [];
      return (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-1.5">{c.signals?.map((s: string) => <Badge key={s} tone="violet">{s}</Badge>)}</div>
          <div className="overflow-hidden rounded-lg ring-1 ring-slate-200">
            <table className="w-full text-[13px]">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr><th className="px-2 py-1 font-medium">Field</th><th className="px-2 py-1 font-medium">{a?.key} <span className="font-normal">({a?.files?.join(", ")})</span></th><th className="px-2 py-1 font-medium">{b?.key} <span className="font-normal">({b?.files?.join(", ")})</span></th></tr>
              </thead>
              <tbody>
                {c.compare?.map((row: any) => (
                  <tr key={row.field} className={cx("border-t border-slate-100", !row.same && "bg-amber-50/60")}>
                    <td className="px-2 py-1 text-slate-500">{row.label}</td>
                    <td className="px-2 py-1 font-mono text-[12px]">{fmtValue(row.a)}</td>
                    <td className="px-2 py-1 font-mono text-[12px]">{fmtValue(row.b)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    }
    case "validation":
      return (
        <div className="space-y-2">
          <RecordSummary r={c.record} />
          <ul className="space-y-0.5 text-sm text-rose-700">{c.issues?.map((i: any, k: number) => <li key={k}>• {i.message}</li>)}</ul>
        </div>
      );
    case "push_failure":
      return (
        <div className="space-y-2">
          <RecordSummary r={c.record} />
          <div className="text-sm"><Badge tone="rose">HTTP {c.http_status}</Badge> <span className="text-slate-600">{c.message}</span></div>
          {c.existing && Object.keys(c.existing).length > 0 && (
            <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
              Already in target: <span className="font-mono">{c.existing.employee_id}</span> - {c.existing.first_name} {c.existing.last_name}, {c.existing.email}, {c.existing.department}, joined {c.existing.date_of_joining}
            </div>
          )}
        </div>
      );
    default:
      return null;
  }
}

function Samples({ label, values }: { label: string; values: string[] }) {
  return (
    <div>
      <div className="mb-1 text-xs text-slate-500">{label}</div>
      <div className="flex flex-wrap gap-1">
        {values?.map((v) => <span key={v} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-700">{v}</span>)}
      </div>
    </div>
  );
}

function RecordSummary({ r }: { r: any }) {
  if (!r) return null;
  const entries = Object.entries(r.data ?? {}).filter(([k]) => !["first_name", "last_name", "employee_id"].includes(k));
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono text-xs text-slate-500">{r.key}</span>
        <span className="font-semibold text-slate-800">{r.name}</span>
        {r.files?.map((f: string) => <Badge key={f}>{f}</Badge>)}
      </div>
      <div className="mt-1 grid grid-cols-1 gap-x-4 text-xs text-slate-600 sm:grid-cols-2">
        {entries.map(([k, v]) => <div key={k} className="truncate"><span className="text-slate-400">{k.replace(/_/g, " ")}:</span> {fmtValue(v)}</div>)}
      </div>
    </div>
  );
}
