import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, RotateCcw, Shuffle, UserRound } from "lucide-react";
import type { ActivityEvent, AIStatus, AuditEntry, Escalation, Mapping, RecordRow, Rule, Run, SchemaField } from "./api";
import { api } from "./api";
import { ActivityFeed } from "./components/ActivityFeed";
import { AIStatusBadge, NoAIBanner } from "./components/AIStatus";
import { AuditView } from "./components/AuditView";
import { EscalationQueue } from "./components/EscalationQueue";
import { MappingsView } from "./components/MappingsView";
import { NewRunDialog } from "./components/NewRunDialog";
import { RecordsView } from "./components/RecordsView";
import { RunHeader } from "./components/RunHeader";
import { RulesView } from "./components/RulesView";
import { PolicyView, TargetView } from "./components/SideViews";
import { Button, Card, RUN_STATUS, cx } from "./components/ui";

type Tab = "queue" | "mapping" | "records" | "audit" | "target" | "rules" | "policy";

function useStored(key: string, initial: string): [string, (v: string) => void] {
  const [v, setV] = useState(() => {
    try { return localStorage.getItem(key) ?? initial; } catch { return initial; }
  });
  return [v, (nv: string) => { setV(nv); try { localStorage.setItem(key, nv); } catch { /* ignore */ } }];
}

export default function App() {
  const [ai, setAi] = useState<AIStatus | null>(null);
  const [schema, setSchema] = useState<SchemaField[]>([]);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [records, setRecords] = useState<RecordRow[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [tab, setTab] = useState<Tab>("queue");
  const [refreshKey, setRefreshKey] = useState(0);
  const [live, setLive] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [actor, setActor] = useStored("migration-agent-actor", "Consultant");
  const bumpTimer = useRef<number | undefined>(undefined);

  const bump = useCallback(() => {
    window.clearTimeout(bumpTimer.current);
    bumpTimer.current = window.setTimeout(() => setRefreshKey((k) => k + 1), 350);
  }, []);

  // AI provider status: on load, every 15 s, and whenever the run moves on
  useEffect(() => {
    const load = () => api.aiStatus().then(setAi).catch(() => undefined);
    load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, []);
  useEffect(() => { if (refreshKey) api.aiStatus().then(setAi).catch(() => undefined); }, [refreshKey]);

  useEffect(() => {
    api.schema().then((s) => setSchema(s.fields));
    api.runs().then((r) => { setRuns(r); if (r.length) setRunId(r[0].id); });
  }, []);

  // live activity: initial history, then server-sent events
  useEffect(() => {
    if (runId === null) return;
    let es: EventSource | null = null;
    let cancelled = false;
    setEvents([]);
    api.events(runId).then((initial) => {
      if (cancelled) return;
      setEvents(initial);
      const after = initial.length ? initial[initial.length - 1].id : 0;
      es = new EventSource(`/api/runs/${runId}/stream?after=${after}`);
      es.onopen = () => setLive(true);
      es.onerror = () => setLive(false);
      es.addEventListener("activity", (msg) => {
        const ev: ActivityEvent = JSON.parse((msg as MessageEvent).data);
        setEvents((prev) => (prev.length && prev[prev.length - 1].id >= ev.id ? prev : [...prev, ev]));
        bump();
      });
    });
    return () => { cancelled = true; es?.close(); setLive(false); };
  }, [runId, bump]);

  // everything else is re-read whenever something happened
  useEffect(() => {
    if (runId === null) return;
    api.run(runId).then(setRun);
    api.escalations(runId).then(setEscalations);
    api.mappings(runId).then(setMappings);
    api.records(runId).then(setRecords);
    api.audit(runId).then(setAudit);
    api.rules().then(setRules);
    api.runs().then(setRuns);
  }, [runId, refreshKey]);

  // safety net if the event stream is blocked by a proxy
  useEffect(() => {
    if (!run || live || !["running", "queued"].includes(run.status)) return;
    const t = window.setInterval(() => setRefreshKey((k) => k + 1), 3000);
    return () => window.clearInterval(t);
  }, [run, live]);

  const openCount = escalations.filter((e) => e.status === "open").length;
  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "queue", label: "Needs your decision", badge: openCount },
    { id: "mapping", label: "Column mapping" },
    { id: "records", label: `Records (${records.filter((r) => r.status !== "merged").length})` },
    { id: "audit", label: "Audit trail" },
    { id: "target", label: "Target system" },
    { id: "rules", label: `Learned rules (${rules.length})` },
    { id: "policy", label: "How it decides" },
  ];

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-600 text-white"><Shuffle className="h-5 w-5" /></div>
          <div>
            <div className="font-semibold leading-tight text-slate-900">Migration Agent</div>
            <div className="text-xs text-slate-500">Legacy HR exports → new HR platform</div>
          </div>
        </div>
        {runs && runs.length > 0 && (
          <select value={runId ?? ""} onChange={(e) => { setRunId(Number(e.target.value)); setTab("queue"); }}
            className="ml-2 rounded-lg border-0 py-1.5 pr-8 pl-2.5 text-sm ring-1 ring-slate-200">
            {runs.map((r) => (
              <option key={r.id} value={r.id}>Run #{r.id} · {r.label.replace("Sample client ", "")} · {RUN_STATUS[r.status]?.label ?? r.status}</option>
            ))}
          </select>
        )}
        <Button variant="primary" onClick={() => setShowNew(true)}><Plus className="h-4 w-4" />New migration</Button>
        <div className="ml-auto flex items-center gap-3">
          <AIStatusBadge status={ai} onChange={setAi} />
          <label className="flex items-center gap-1.5 text-sm text-slate-500">
            <UserRound className="h-4 w-4" />
            <input value={actor} onChange={(e) => setActor(e.target.value)} title="Your name - recorded in the audit trail"
              className="w-32 rounded-lg border-0 px-2 py-1 text-sm text-slate-800 ring-1 ring-slate-200" />
          </label>
          <button title="Reset demo: delete all runs, learned rules and target data"
            onClick={async () => {
              if (!confirm("Reset the demo? This deletes all runs, learned rules and everything in the target system.")) return;
              await api.reset();
              setRuns([]); setRunId(null); setRun(null); setEvents([]); setRules([]);
            }}
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><RotateCcw className="h-4 w-4" /></button>
        </div>
      </header>
      <NoAIBanner status={ai} onChange={setAi} />

      {runs && runs.length === 0 && !runId ? (
        <Welcome onStart={() => setShowNew(true)} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <main className="scroll-thin min-w-0 flex-1 overflow-y-auto p-5">
            {run && (
              <div className="mx-auto max-w-6xl space-y-4">
                <RunHeader run={run} />
                <div className="flex flex-wrap gap-1 border-b border-slate-200">
                  {tabs.map((t) => (
                    <button key={t.id} onClick={() => setTab(t.id)}
                      className={cx("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition",
                        tab === t.id ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-500 hover:text-slate-800")}>
                      {t.label}
                      {t.badge ? <span className="rounded-full bg-amber-500 px-1.5 text-xs text-white">{t.badge}</span> : null}
                    </button>
                  ))}
                </div>
                {tab === "queue" && <EscalationQueue run={run} items={escalations} actor={actor} onChanged={bump} />}
                {tab === "mapping" && <MappingsView mappings={mappings} />}
                {tab === "records" && <RecordsView run={run} records={records} schema={schema} actor={actor} onChanged={bump} />}
                {tab === "audit" && <AuditView entries={audit} runId={run.id} />}
                {tab === "target" && <TargetView refreshKey={refreshKey} />}
                {tab === "rules" && <RulesView rules={rules} actor={actor} onChanged={() => { api.rules().then(setRules); bump(); }} />}
                {tab === "policy" && <PolicyView />}
              </div>
            )}
          </main>
          <aside className="hidden w-[400px] shrink-0 border-l border-slate-200 bg-white lg:block">
            <ActivityFeed events={events} live={live} />
          </aside>
        </div>
      )}

      {showNew && (
        <NewRunDialog onClose={() => setShowNew(false)}
          onStarted={(id) => { setShowNew(false); setRunId(id); setTab("queue"); setRefreshKey((k) => k + 1); }} />
      )}
    </div>
  );
}

function Welcome({ onStart }: { onStart: () => void }) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <Card className="max-w-xl p-8 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-600 text-white"><Shuffle className="h-6 w-6" /></div>
        <h1 className="mt-4 text-xl font-semibold text-slate-900">Move a client's employee data into the new platform</h1>
        <p className="mt-2 text-sm text-slate-600">
          The agent reads the client's messy exports, works out which column is which, cleans and combines the records, and pushes them
          to the new system. It stops to ask you only when the data can't prove the answer - and remembers your decisions for next time.
        </p>
        <Button variant="primary" className="mt-6" onClick={onStart}><Plus className="h-4 w-4" />Start a migration</Button>
      </Card>
    </div>
  );
}
