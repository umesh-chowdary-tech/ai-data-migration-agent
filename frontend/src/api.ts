export type RunStatus =
  | "queued" | "running" | "waiting_for_input" | "needs_review" | "completed" | "attention" | "failed" | "rolled_back";

export interface Summary {
  records: number; created: number; updated: number; unchanged: number; failed: number; skipped: number;
  merged: number; needs_review: number; rolled_back: number; escalations_open: number; escalations_total: number;
  escalations_blocking: number; auto_decisions: number; auto_fixes: number; by_status: Record<string, number>;
}

export interface RunAI {
  used: Record<string, number>; calls: number; no_ai_calls: number;
  failures: { provider: string; model: string; error: string; kind: string; purpose: string; ts: number }[];
  mode_at_start?: "ai" | "fallback" | "none"; label_at_start?: string; reason_at_start?: string | null;
}

export interface Run {
  id: number; label: string; created_at: number; status: RunStatus; stage: string; error: string | null;
  finished_at: number | null; files: string[]; summary: Summary; ai: RunAI | null;
}

export interface AIProvider {
  name: string; label: string; model: string; status: "untested" | "ok" | "failed" | "not_configured";
  configured: boolean; error: string | null; error_kind: string | null; checked_at: number | null;
  calls: number; failures: number; retry_in: number;
}
export interface AIStatus {
  mode: "ai" | "fallback" | "none"; label: string; available: boolean; active: string | null;
  reason: string | null; providers: AIProvider[];
}

export interface ActivityEvent {
  id: number; ts: number; stage: string; level: "info" | "auto" | "escalation" | "human" | "success" | "warning" | "error";
  category: string | null; message: string; data: any;
}

export interface Option { label: string; value: any; detail?: string; files?: string[] }
export interface FormField {
  name: string; label: string; type: string; value: any; options: string[] | null; required: boolean;
  suggestion?: any; why?: string | null;
}

export interface Escalation {
  id: number; run_id: number; type: string; type_label: string; status: string; blocking: boolean;
  title: string; question: string; reason: string; context: any;
  proposal: { label: string; value: any; confidence?: number; details?: string[] } | null;
  correct:
    | { kind: "choice"; options: Option[]; more?: Option[]; other?: { field: string; label: string; type: string; options: string[] | null } }
    | { kind: "fields"; fields: FormField[] }
    | null;
  reject_label: string; record_keys: string[]; resolution: any; resolved_by: string | null;
  created_at: number; resolved_at: number | null;
  /** what "remember" can mean for this card: this employee only, or this kind of problem for everyone */
  remember_options?: { value: "record" | "problem"; label: string; field?: string }[] | null;
}

export interface Mapping {
  id: number; file: string; source_column: string; target_field: string | null; target_label: string | null;
  confidence: number; method: string; status: string; reason: string; date_format: string | null;
  samples: string[]; candidates: { field: string; label: string; score: number; evidence: string[] }[];
}

export interface Change { field: string; from: any; to: any; reason: string; by: string; ts: number; file?: string }
export interface RecordRow {
  key: string; name: string; status: string; data: Record<string, any>;
  rows: { file: string; row: number; raw: Record<string, any> }[]; field_sources: Record<string, string>;
  changes: Change[]; issues: any[]; validation_attempts: number; push_op: string | null; push_attempts: number;
  last_error: string | null;
}

export interface AuditEntry {
  id: number; ts: number; actor: string; action: string; entity: string; before: any; after: any; reason: string;
}

export interface Rule {
  id: number; kind: string; key: string; value: any; description: string; created_by: string; created_at: number;
  source_run: number | null; times_applied: number; origin: "decision" | "manual"; reason: string | null;
  updated_at: number | null; updated_by: string | null;
}

export interface RuleEvent {
  id: number; ts: number; actor: string; action: "created" | "edited" | "deleted"; before: any; after: any;
  reason: string | null; review: any;
}

export interface RuleFact { id: string; level: "info" | "ok" | "warn"; text: string }
export interface Cited { text: string; cites: string[] }
export interface RuleReview {
  id: string; needed: "yes" | "no" | "overrides" | "unknown"; needs_acknowledgement: boolean; facts: RuleFact[];
  proposal: { kind: string; key: string; value: any; description: string; rule_id: number | null; before: any };
  ai: {
    available: boolean; reason?: string; model?: string; fell_back?: boolean;
    verdict?: "recommend" | "caution" | "not_recommended"; needed?: "yes" | "no" | "unclear"; summary?: string;
    effects?: Cited[]; risks?: Cited[];
  };
}

export interface RulesContext {
  fields: { name: string; label: string; type: string; values: string[]; required: boolean;
    problems: { code: string; label: string }[] }[];
  targets: { name: string; label: string }[];
  headers: { file: string; column: string; target: string | null; run_id: number; is_date: boolean }[];
  files: string[];
}

export interface SchemaField { name: string; label: string; type: string; required: boolean; values: string[] }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = body.detail || msg;
    } catch { /* not json */ }
    throw new Error(msg);
  }
  return res.json();
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  aiStatus: () => request<AIStatus>("/api/ai/status"),
  aiCheck: () => post<AIStatus>("/api/ai/check"),
  policy: () => request<{ policy: { area: string; auto: string; ask: string; why: string }[]; thresholds: Record<string, number> }>("/api/policy"),
  schema: () => request<{ fields: SchemaField[] }>("/api/schema"),
  samples: () => request<{ id: string; label: string; files: string[] }[]>("/api/samples"),
  runs: () => request<Run[]>("/api/runs"),
  run: (id: number) => request<Run>(`/api/runs/${id}`),
  events: (id: number, after = 0) => request<ActivityEvent[]>(`/api/runs/${id}/events?after=${after}`),
  escalations: (id: number) => request<Escalation[]>(`/api/runs/${id}/escalations`),
  mappings: (id: number) => request<Mapping[]>(`/api/runs/${id}/mappings`),
  records: (id: number) => request<RecordRow[]>(`/api/runs/${id}/records`),
  audit: (id: number) => request<AuditEntry[]>(`/api/runs/${id}/audit`),
  rules: () => request<Rule[]>("/api/rules"),
  rulesContext: () => request<RulesContext>("/api/rules/context"),
  reviewRule: (proposal: Record<string, any>) => post<RuleReview>("/api/rules/review", proposal),
  saveRule: (body: { review_id: string; reason: string; actor: string; acknowledge: boolean }) =>
    post<Rule>("/api/rules/save", body),
  ruleHistory: (id: number) => request<RuleEvent[]>(`/api/rules/${id}/history`),
  generalizeRule: (id: number) =>
    request<{ field: string; problem: string; description: string; replaces: number; note: string | null }>(`/api/rules/${id}/generalize`),
  deleteRule: (id: number, actor: string, reason: string) =>
    request(`/api/rules/${id}?actor=${encodeURIComponent(actor)}&reason=${encodeURIComponent(reason)}`, { method: "DELETE" }),
  target: () => request<Record<string, any>[]>("/api/target/employees"),
  targetStatus: () => request<{ outage: boolean; outage_seconds_left: number }>("/api/target/status"),
  outage: (seconds: number) => post(`/api/target/outage?seconds=${seconds}`),
  resolve: (id: number, body: { action: string; value?: any; note?: string; remember?: boolean; scope?: string; actor: string }) =>
    post<Escalation>(`/api/escalations/${id}/resolve`, body),
  retry: (runId: number, keys: string[] | null, actor: string) => post(`/api/runs/${runId}/retry`, { keys, actor }),
  rollback: (runId: number, keys: string[] | null, actor: string) => post(`/api/runs/${runId}/rollback`, { keys, actor }),
  reset: () => post("/api/reset"),
  startSample: (sample: string) => {
    const fd = new FormData();
    fd.append("sample", sample);
    return request<{ id: number }>("/api/runs", { method: "POST", body: fd });
  },
  startUpload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return request<{ id: number }>("/api/runs", { method: "POST", body: fd });
  },
};

export const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const fmtValue = (v: any): string => {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return v >= 10000 ? v.toLocaleString("en-IN") : String(v);
  if (Array.isArray(v)) return v.map(fmtValue).join(" | ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};
