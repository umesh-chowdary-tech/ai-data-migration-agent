import { useEffect, useState } from "react";
import { CloudOff, Database, Hand, Sparkles } from "lucide-react";
import { api, fmtValue } from "../api";
import { Badge, Button, Card } from "./ui";

export function TargetView({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<Record<string, any>[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [outage, setOutage] = useState<{ outage: boolean; outage_seconds_left: number } | null>(null);

  useEffect(() => {
    api.target().then((r) => { setRows(r); setErr(null); }).catch((e) => setErr(e.message));
    api.targetStatus().then(setOutage).catch(() => undefined);
  }, [refreshKey]);

  useEffect(() => {
    if (!outage?.outage) return;
    const t = setInterval(() => api.targetStatus().then(setOutage).catch(() => undefined), 1000);
    return () => clearInterval(t);
  }, [outage?.outage]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <Database className="h-4 w-4 text-slate-400" />
          What the new HR platform (mock API) currently holds: <span className="font-semibold text-slate-900">{rows.length}</span> employees
        </div>
        <div className="flex items-center gap-2">
          {outage?.outage && <Badge tone="rose">Outage - {Math.ceil(outage.outage_seconds_left)}s left</Badge>}
          <Button size="sm" variant="danger" title="Makes the target return 503 for 20 seconds, to demonstrate retry + failure handling"
            onClick={async () => { await api.outage(20); setOutage(await api.targetStatus()); }}>
            <CloudOff className="h-3.5 w-3.5" />Simulate 20s outage
          </Button>
        </div>
      </div>
      {err && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{err}</div>}
      <Card className="overflow-hidden">
        <table className="w-full text-[13px]">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>{["ID", "Name", "Email", "Department", "Joined", "Type", "Manager", "Status"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.employee_id} className="border-t border-slate-100">
                <td className="px-3 py-1.5 font-mono text-xs">{r.employee_id}</td>
                <td className="px-3 py-1.5">{r.first_name} {r.last_name}</td>
                <td className="px-3 py-1.5 text-slate-600">{r.email}</td>
                <td className="px-3 py-1.5">{r.department}</td>
                <td className="px-3 py-1.5 font-mono text-xs">{r.date_of_joining}</td>
                <td className="px-3 py-1.5">{fmtValue(r.employment_type)}</td>
                <td className="px-3 py-1.5 text-xs text-slate-500">{fmtValue(r.manager_email)}</td>
                <td className="px-3 py-1.5">{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

export function PolicyView() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.policy>> | null>(null);
  useEffect(() => { api.policy().then(setData); }, []);
  if (!data) return null;
  return (
    <div className="space-y-3">
      <div className="rounded-xl bg-indigo-50 px-4 py-3 text-sm text-indigo-900 ring-1 ring-indigo-100">
        <span className="font-semibold">The rule of thumb:</span> I act alone when the data itself proves the answer (an exact header, a value that can only be read one way,
        an exact duplicate). The AI model may <i>suggest</i>, but its opinion alone is never enough to change your client's data - that's when I ask.
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {data.policy.map((p) => (
          <Card key={p.area} className="p-4">
            <div className="font-semibold text-slate-900">{p.area}</div>
            <div className="mt-2 flex gap-2 text-sm"><Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" /><div><span className="font-medium text-violet-700">I handle it: </span>{p.auto}</div></div>
            <div className="mt-1.5 flex gap-2 text-sm"><Hand className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" /><div><span className="font-medium text-amber-700">I ask you: </span>{p.ask}</div></div>
            <div className="mt-2 text-xs text-slate-500">Why: {p.why}</div>
          </Card>
        ))}
      </div>
      <div className="text-xs text-slate-400">
        Thresholds: auto-map at ≥{Math.round(data.thresholds.map_auto_min * 100)}% with a ≥{Math.round(data.thresholds.map_min_gap * 100)}-point lead over the runner-up ·
        ignore below {Math.round(data.thresholds.map_ignore_below * 100)}% · {data.thresholds.date_evidence_min} cross-file matches to settle a date format ·
        {" "}{data.thresholds.validation_attempts} validation attempts · {data.thresholds.push_attempts} push attempts
      </div>
    </div>
  );
}
