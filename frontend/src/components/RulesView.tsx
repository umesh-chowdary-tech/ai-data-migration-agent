import { useEffect, useState } from "react";
import {
  AlertTriangle, Bot, BotOff, Brain, CheckCircle2, ChevronDown, ChevronRight, History, Info, Pencil, Plus,
  ShieldCheck, Trash2, X,
} from "lucide-react";
import type { Rule, RuleEvent, RuleReview, RulesContext } from "../api";
import { api, fmtTime, fmtValue } from "../api";
import { Badge, Button, Card, Empty, cx } from "./ui";

const KIND: Record<string, string> = {
  column_map: "Column mapping", date_format: "Date format", value_map: "Value fix", record_value: "Employee fix",
  source_priority: "Source of truth", duplicate: "Duplicate decision", skip_record: "Skip employee",
  issue_policy: "Problem rule",
};
const PATTERN_KINDS = ["column_map", "date_format", "value_map", "source_priority", "issue_policy"];
const NOT_EDITABLE: Record<string, string> = {
  skip_record: "Skip rules can only be deleted",
  issue_policy: "A problem rule has one action (leave it empty) - delete it to go back to asking",
};

export function RulesView({ rules, actor, onChanged }: { rules: Rule[]; actor: string; onChanged: () => void }) {
  const [editor, setEditor] = useState<{ rule?: Rule } | null>(null);
  const [openHistory, setOpenHistory] = useState<number | null>(null);
  const [ctx, setCtx] = useState<RulesContext | null>(null);
  useEffect(() => { api.rulesContext().then(setCtx); }, [rules.length]);
  const freeForm = (r: Rule) => {
    if (r.kind !== "value_map") return false;
    const f = ctx?.fields.find((x) => x.name === r.key.split("|")[0]);
    return !!f && !f.values.length;
  };

  async function remove(r: Rule) {
    const reason = window.prompt(`Delete "${r.description}"?\n\nThe agent will ask about this again next time. Reason (kept in the history):`);
    if (reason === null) return;
    await api.deleteRule(r.id, actor, reason);
    onChanged();
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-slate-500">
          Every rule lets the agent decide something on its own next time instead of asking. Rules come from your decisions on
          cards, or can be added by hand. Every new or edited rule is <span className="font-medium text-slate-700">checked
          against your data and reviewed by the AI</span> before it can be saved.
        </p>
        <Button variant="primary" onClick={() => setEditor({})}><Plus className="h-4 w-4" />Add rule</Button>
      </div>

      {!rules.length ? (
        <Empty icon={<Brain className="h-10 w-10" />} title="No learned rules yet">
          Resolve a card with "Remember for future runs" ticked, or add a rule by hand.
        </Empty>
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr><th className="px-3 py-2 font-medium">Type</th><th className="px-2 py-2 font-medium">Rule</th>
                <th className="px-2 py-2 font-medium">Origin</th><th className="px-2 py-2 font-medium">Used</th><th /></tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <RuleRow key={r.id} r={r} open={openHistory === r.id} freeForm={freeForm(r)}
                  onToggle={() => setOpenHistory(openHistory === r.id ? null : r.id)}
                  onEdit={() => setEditor({ rule: r })} onDelete={() => remove(r)} />
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {editor && (
        <RuleEditor rule={editor.rule} actor={actor} onClose={() => setEditor(null)}
          onSaved={() => { setEditor(null); onChanged(); }} />
      )}
    </div>
  );
}

function RuleRow({ r, open, freeForm, onToggle, onEdit, onDelete }: {
  r: Rule; open: boolean; freeForm: boolean; onToggle: () => void; onEdit: () => void; onDelete: () => void;
}) {
  const [events, setEvents] = useState<RuleEvent[] | null>(null);
  useEffect(() => { if (open) api.ruleHistory(r.id).then(setEvents); }, [open, r.id, r.updated_at]);
  return (
    <>
      <tr className="border-t border-slate-100 align-top">
        <td className="px-3 py-2"><Badge tone="sky">{KIND[r.kind] ?? r.kind}</Badge></td>
        <td className="px-2 py-2 text-slate-700">
          {r.description}
          {freeForm && (
            <div className="mt-0.5 flex items-center gap-1 text-xs text-amber-700"
              title="It only fires for this exact text, and gives everyone with that text the same value - possibly someone else's. Edit it to make it generic.">
              <AlertTriangle className="h-3.5 w-3.5" />Exact value on a free-form field - consider making it generic
            </div>
          )}
          {r.reason && <div className="text-xs text-slate-400">Why: {r.reason}</div>}
        </td>
        <td className="px-2 py-2 text-xs text-slate-500">
          {r.origin === "manual"
            ? <Badge tone="violet">Added by hand</Badge>
            : <span>From a decision{r.source_run ? ` in run #${r.source_run}` : ""}</span>}
          <div>{r.created_by} · {fmtTime(r.created_at)}{r.updated_by ? ` · edited by ${r.updated_by}` : ""}</div>
        </td>
        <td className="px-2 py-2 text-xs text-slate-500">{r.times_applied}×</td>
        <td className="px-2 py-2">
          <div className="flex justify-end gap-1">
            <IconBtn title="History" onClick={onToggle}><History className="h-4 w-4" /></IconBtn>
            <IconBtn title={NOT_EDITABLE[r.kind] ?? "Edit"} disabled={!!NOT_EDITABLE[r.kind]}
              onClick={onEdit}><Pencil className="h-4 w-4" /></IconBtn>
            <IconBtn title="Delete (the agent will ask again)" danger onClick={onDelete}><Trash2 className="h-4 w-4" /></IconBtn>
          </div>
        </td>
      </tr>
      {open && (
        <tr className="bg-slate-50/60">
          <td colSpan={5} className="px-4 py-2">
            {!events ? <span className="text-xs text-slate-400">Loading...</span> : (
              <ol className="space-y-1">
                {events.map((e) => (
                  <li key={e.id} className="text-xs text-slate-600">
                    <span className="font-medium text-slate-800">{e.action}</span> by {e.actor} · {fmtTime(e.ts)}
                    {e.action !== "created" && <> · <span className="font-mono">{fmtValue(e.before)}</span> → <span className="font-mono">{fmtValue(e.after)}</span></>}
                    {e.reason && <> · “{e.reason}”</>}
                    {e.review && (
                      <span className="ml-1 text-slate-400">
                        · reviewed: {e.review.needed === "no" ? "not needed" : e.review.needed}
                        {e.review.ai?.available ? `, AI said ${e.review.ai.verdict?.replace("_", " ")}` : ", no AI"}
                        {e.review.acknowledged_warnings ? ", warnings acknowledged" : ""}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function IconBtn({ children, title, onClick, danger, disabled }: {
  children: React.ReactNode; title: string; onClick: () => void; danger?: boolean; disabled?: boolean;
}) {
  return (
    <button title={title} disabled={disabled} onClick={onClick}
      className={cx("rounded p-1 text-slate-400 disabled:opacity-30", danger ? "hover:bg-rose-50 hover:text-rose-600" : "hover:bg-slate-100 hover:text-slate-700")}>
      {children}
    </button>
  );
}

// ------------------------------------------------------------------------------------------------
// Add / edit a rule: form -> "Check this rule" (data checks + AI review) -> reason -> save
// ------------------------------------------------------------------------------------------------

function RuleEditor({ rule, actor, onClose, onSaved }: { rule?: Rule; actor: string; onClose: () => void; onSaved: () => void }) {
  const [ctx, setCtx] = useState<RulesContext | null>(null);
  const [kind, setKind] = useState<string>(rule?.kind ?? "column_map");
  const [form, setForm] = useState<Record<string, any>>(() => initialForm(rule));
  const [review, setReview] = useState<RuleReview | null>(null);
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // "make it generic": turn an exact-value rule into a problem rule that replaces it
  const [replaces, setReplaces] = useState<number | null>(null);
  const [genNote, setGenNote] = useState<string | null>(null);
  useEffect(() => { api.rulesContext().then(setCtx); }, []);

  const set = (k: string, v: any) => { setForm((f) => ({ ...f, [k]: v })); setReview(null); setAck(false); };
  const field = ctx?.fields.find((f) => f.name === form.field);
  const editing = !!rule && !replaces;
  const freeFormExact = editing && kind === "value_map" && !!field && !field.values.length;

  async function makeGeneric() {
    if (!rule) return;
    setError(null);
    try {
      const g = await api.generalizeRule(rule.id);
      setKind("issue_policy"); setForm({ field: g.field, problem: g.problem });
      setReplaces(g.replaces); setGenNote(g.note); setReview(null); setAck(false);
    } catch (e: any) { setError(e.message); }
  }

  async function check() {
    setBusy("review"); setError(null);
    try {
      const proposal = replaces ? { kind, ...form, replaces } : editing ? { rule_id: rule!.id, ...form } : { kind, ...form };
      setReview(await api.reviewRule(proposal));
    } catch (e: any) { setError(e.message); } finally { setBusy(null); }
  }
  async function save() {
    if (!review) return;
    setBusy("save"); setError(null);
    try { await api.saveRule({ review_id: review.id, reason, actor, acknowledge: ack }); onSaved(); }
    catch (e: any) { setError(e.message); setBusy(null); }
  }

  const input = "rounded-lg border-0 px-2.5 py-1.5 text-sm ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500";
  const canSave = review && reason.trim().length >= 5 && (!review.needs_acknowledgement || ack);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4" onClick={onClose}>
      <div className="my-8 w-full max-w-2xl rounded-2xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <div className="text-lg font-semibold text-slate-900">{replaces ? "Make the rule generic" : rule ? "Edit rule" : "Add a rule"}</div>
            <div className="text-sm text-slate-500">The agent will apply it automatically in future runs.</div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"><X className="h-5 w-5" /></button>
        </div>

        <div className="space-y-4 px-5 py-4">
          {!rule && (
            <div className="flex flex-wrap gap-1.5">
              {PATTERN_KINDS.map((k) => (
                <button key={k} onClick={() => { setKind(k); setForm({}); setReview(null); }}
                  className={cx("rounded-lg px-3 py-1.5 text-sm font-medium ring-1", kind === k ? "bg-indigo-50 text-indigo-700 ring-indigo-300" : "text-slate-600 ring-slate-200 hover:bg-slate-50")}>
                  {KIND[k]}
                </button>
              ))}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
            {kind === "column_map" && (<>
              When a column is named
              <input list="rule-headers" disabled={!!rule} value={form.header ?? ""} onChange={(e) => set("header", e.target.value)} className={cx(input, "w-44")} placeholder="e.g. Contact Number" />
              map it to
              <select value={form.target ?? ""} onChange={(e) => set("target", e.target.value || null)} className={input}>
                <option value="">— don't migrate it —</option>
                {ctx?.targets.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
              </select>
            </>)}
            {kind === "date_format" && (<>
              When a column named
              <input list="rule-date-headers" disabled={!!rule} value={form.header ?? ""} onChange={(e) => set("header", e.target.value)} className={cx(input, "w-44")} placeholder="e.g. Joining Date" />
              is ambiguous, read it as
              <select value={form.format ?? ""} onChange={(e) => set("format", e.target.value)} className={input}>
                <option value="">choose...</option>
                <option value="DMY">day-first (DD/MM/YYYY)</option>
                <option value="MDY">month-first (MM/DD/YYYY)</option>
              </select>
            </>)}
            {kind === "issue_policy" && (<>
              Whenever
              <select disabled={!!replaces} value={form.field ?? ""} onChange={(e) => { set("field", e.target.value); set("problem", ""); }} className={input}>
                <option value="">an optional field...</option>
                {ctx?.fields.filter((f) => f.problems.length).map((f) => <option key={f.name} value={f.name}>{f.label}</option>)}
              </select>
              <select value={form.problem ?? ""} onChange={(e) => set("problem", e.target.value)} className={input}>
                <option value="">has a problem...</option>
                {field?.problems.map((p) => <option key={p.code} value={p.code}>{p.label}</option>)}
              </select>
              <span>→ <span className="font-medium">leave it empty</span> instead of asking, for every employee</span>
            </>)}
            {kind === "value_map" && (<>
              When
              <select disabled={!!rule} value={form.field ?? ""} onChange={(e) => set("field", e.target.value)} className={input}>
                <option value="">a category field...</option>
                {ctx?.fields.filter((f) => f.values.length || f.name === form.field).map((f) => <option key={f.name} value={f.name}>{f.label}</option>)}
              </select>
              is
              <input disabled={!!rule} value={form.raw ?? ""} onChange={(e) => set("raw", e.target.value)} className={cx(input, "w-40")} placeholder="value in the file" />
              use
              <ValueInput field={field} value={form.value} onChange={(v) => set("value", v)} className={input} />
            </>)}
            {kind === "source_priority" && (<>
              When files disagree on
              <select disabled={!!rule} value={form.field ?? ""} onChange={(e) => set("field", e.target.value)} className={input}>
                <option value="">a field...</option>
                {ctx?.fields.map((f) => <option key={f.name} value={f.name}>{f.label}</option>)}
              </select>
              trust
              <select value={form.file ?? ""} onChange={(e) => set("file", e.target.value)} className={input}>
                <option value="">a file...</option>
                {ctx?.files.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </>)}
            {kind === "record_value" && (<>
              For <span className="font-mono">{rule?.key.split("|")[0]}</span>, always set {ctx?.fields.find((f) => f.name === rule?.key.split("|")[1])?.label} to
              <ValueInput field={ctx?.fields.find((f) => f.name === rule?.key.split("|")[1])} value={form.value}
                onChange={(v) => set("value", v)} className={input} />
            </>)}
            {kind === "duplicate" && (<>
              <span className="font-mono">{rule?.key.replace("|", " & ")}</span> are
              <select value={form.value ?? ""} onChange={(e) => set("value", e.target.value)} className={input}>
                <option value="merge">the same person (merge)</option>
                <option value="keep_both">different people (keep both)</option>
              </select>
            </>)}
            <datalist id="rule-headers">{ctx?.headers.map((h) => <option key={h.file + h.column} value={h.column}>{h.file}</option>)}</datalist>
            <datalist id="rule-date-headers">{ctx?.headers.filter((h) => h.is_date).map((h) => <option key={h.file + h.column} value={h.column}>{h.file}</option>)}</datalist>
          </div>

          {freeFormExact && (
            <div className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900 ring-1 ring-amber-200">
              <div className="font-medium">This rule only fires for the exact text "{form.raw}".</div>
              <div className="mt-0.5 text-amber-800">
                For a free-form field like {field?.label.toLowerCase()} every bad value belongs to one person, so an exact-value rule
                rarely applies again - and could give someone else this value. Make it generic: <i>whenever {field?.label.toLowerCase()} has
                this problem, leave it empty</i>.
              </div>
              <Button size="sm" className="mt-2" onClick={makeGeneric}>Make it generic</Button>
            </div>
          )}
          {replaces && (
            <div className="rounded-xl bg-indigo-50 px-4 py-3 text-sm text-indigo-900 ring-1 ring-indigo-200">
              Saving this replaces the exact-value rule "{rule?.description}".
              {genNote && <div className="mt-1 text-indigo-800">{genNote}</div>}
            </div>
          )}
          {!review && (
            <Button variant="primary" busy={busy === "review"} onClick={check}>
              <ShieldCheck className="h-4 w-4" />Check this rule
            </Button>
          )}
          {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}
          {review && <ReviewPanel review={review} />}

          {review && (
            <div className="space-y-2 border-t border-slate-100 pt-3">
              <label className="block text-sm font-medium text-slate-700">Why are you {rule ? "changing" : "adding"} this rule? <span className="text-rose-500">*</span></label>
              <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2}
                placeholder="e.g. Confirmed with the client's HR team on 12 Sep"
                className="w-full rounded-lg border-0 px-2.5 py-1.5 text-sm ring-1 ring-slate-200 focus:ring-2 focus:ring-indigo-500" />
              {review.needs_acknowledgement && (
                <label className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5 accent-amber-600" />
                  I've read the warnings above and still want to save this rule.
                </label>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!canSave} busy={busy === "save"} onClick={save}>Save rule</Button>
        </div>
      </div>
    </div>
  );
}

function initialForm(rule?: Rule): Record<string, any> {
  if (!rule) return {};
  switch (rule.kind) {
    case "column_map": return { header: rule.key, target: rule.value };
    case "date_format": return { header: rule.key, format: rule.value };
    case "value_map": { const [field, raw] = rule.key.split("|"); return { field, raw, value: rule.value }; }
    case "source_priority": return { field: rule.key, file: rule.value };
    default: return { value: rule.value };
  }
}

function ValueInput({ field, value, onChange, className }: {
  field?: { type: string; values: string[] }; value: any; onChange: (v: any) => void; className: string;
}) {
  if (field?.values?.length) {
    return (
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={className}>
        <option value="">choose...</option>
        {field.values.map((v) => <option key={v} value={v}>{v}</option>)}
      </select>
    );
  }
  return <input type={field?.type === "date" ? "date" : "text"} value={value ?? ""} onChange={(e) => onChange(e.target.value)}
    className={cx(className, "w-44")} placeholder="correct value" />;
}

const NEEDED: Record<string, { label: string; tone: "emerald" | "amber" | "rose" | "slate" }> = {
  yes: { label: "Needed", tone: "emerald" }, no: { label: "Not needed", tone: "amber" },
  overrides: { label: "Overrides the data", tone: "rose" }, unknown: { label: "Can't tell yet - no matching data", tone: "slate" },
};
const VERDICT: Record<string, { label: string; tone: "emerald" | "amber" | "rose" }> = {
  recommend: { label: "Recommends it", tone: "emerald" }, caution: { label: "Advises caution", tone: "amber" },
  not_recommended: { label: "Advises against it", tone: "rose" },
};

function ReviewPanel({ review }: { review: RuleReview }) {
  const [hl, setHl] = useState<string | null>(null);
  const n = NEEDED[review.needed] ?? NEEDED.unknown;
  const ai = review.ai;
  return (
    <div className="space-y-3">
      <div className="rounded-xl ring-1 ring-slate-200">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
          <span className="text-sm font-semibold text-slate-800">What this rule would do - checked against your data</span>
          <Badge tone={n.tone}>{n.label}</Badge>
        </div>
        <ul className="space-y-1.5 px-4 py-3">
          {review.facts.map((f) => (
            <li key={f.id} className={cx("flex gap-2 rounded-md px-1 text-sm", hl === f.id && "bg-indigo-50")}>
              <span className="mt-0.5 font-mono text-[10px] text-slate-400">{f.id}</span>
              {f.level === "ok" ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                : f.level === "warn" ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                  : <Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />}
              <span className="text-slate-700">{f.text}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-xl ring-1 ring-violet-200">
        <div className="flex items-center justify-between border-b border-violet-100 bg-violet-50/50 px-4 py-2">
          <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-800">
            {ai.available ? <Bot className="h-4 w-4 text-violet-600" /> : <BotOff className="h-4 w-4 text-slate-400" />}AI review
          </span>
          {ai.available && ai.verdict && <Badge tone={VERDICT[ai.verdict].tone}>{VERDICT[ai.verdict].label}</Badge>}
        </div>
        {!ai.available ? (
          <div className="px-4 py-3 text-sm text-slate-600">
            No AI involved in this review ({ai.reason}). The data checks above are the whole review.
          </div>
        ) : (
          <div className="space-y-2 px-4 py-3 text-sm">
            {ai.summary && <p className="text-slate-800">{ai.summary}</p>}
            <CitedList title="Effects" items={ai.effects} onHover={setHl} />
            <CitedList title="Risks" items={ai.risks} onHover={setHl} />
            <p className="text-xs text-slate-400">
              {ai.model}{ai.fell_back ? " (backup provider)" : ""}. The AI only saw the facts above and must cite them; it advises, you decide.
              {ai.needed ? ` It thinks the rule is ${ai.needed === "yes" ? "needed" : ai.needed === "no" ? "not needed" : "possibly needed"}.` : ""}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function CitedList({ title, items, onHover }: { title: string; items?: { text: string; cites: string[] }[]; onHover: (id: string | null) => void }) {
  const [open, setOpen] = useState(true);
  if (!items?.length) return null;
  return (
    <div>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}{title}
      </button>
      {open && (
        <ul className="mt-1 space-y-1">
          {items.map((x, i) => (
            <li key={i} className="flex flex-wrap items-center gap-1.5 text-slate-700">
              <span>• {x.text}</span>
              {x.cites.length ? x.cites.map((c) => (
                <span key={c} onMouseEnter={() => onHover(c)} onMouseLeave={() => onHover(null)}
                  className="cursor-default rounded bg-indigo-50 px-1 font-mono text-[10px] text-indigo-600">{c}</span>
              )) : <span className="rounded bg-slate-100 px-1 text-[10px] text-slate-500" title="The AI gave no fact for this claim">no source cited</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
