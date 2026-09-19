import { ArrowRight, FileSpreadsheet } from "lucide-react";
import type { Mapping } from "../api";
import { Badge, Card, ConfidenceBar, Empty } from "./ui";

const METHOD: Record<string, { label: string; tone: "violet" | "indigo" | "sky" | "slate" | "amber" }> = {
  auto: { label: "Agent", tone: "violet" },
  human: { label: "You", tone: "indigo" },
  rule: { label: "Learned rule", tone: "sky" },
  unmapped: { label: "Not migrated", tone: "slate" },
  escalate: { label: "Waiting for you", tone: "amber" },
};
const DATE_FMT: Record<string, string> = { DMY: "DD/MM/YYYY", MDY: "MM/DD/YYYY", iso: "ISO / Excel date" };

export function MappingsView({ mappings }: { mappings: Mapping[] }) {
  if (!mappings.length) return <Empty title="No mappings yet">They appear as soon as the agent has read the files.</Empty>;
  const files = [...new Set(mappings.map((m) => m.file))];
  return (
    <div className="space-y-4">
      {files.map((file) => {
        const rows = mappings.filter((m) => m.file === file);
        const auto = rows.filter((m) => m.method === "auto" || m.method === "rule").length;
        return (
          <Card key={file} className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
              <div className="flex items-center gap-2 font-medium text-slate-800"><FileSpreadsheet className="h-4 w-4 text-emerald-600" />{file}</div>
              <span className="text-xs text-slate-500">{auto} of {rows.length} columns mapped without asking</span>
            </div>
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-4 py-1.5 font-medium">Source column</th>
                  <th className="py-1.5" />
                  <th className="px-2 py-1.5 font-medium">Target field</th>
                  <th className="px-2 py-1.5 font-medium">Confidence</th>
                  <th className="px-2 py-1.5 font-medium">Decided by</th>
                  <th className="px-2 py-1.5 font-medium">Sample values</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => {
                  const method = m.status === "pending" ? METHOD.escalate : m.status === "ignored" && m.method !== "human" ? METHOD.unmapped : METHOD[m.method] ?? METHOD.auto;
                  return (
                    <tr key={m.id} className="border-t border-slate-100 align-top" title={m.reason}>
                      <td className="px-4 py-2 font-medium text-slate-800">{m.source_column}</td>
                      <td className="py-2 text-slate-300"><ArrowRight className="h-4 w-4" /></td>
                      <td className="px-2 py-2">
                        {m.target_label ? <span className="text-slate-800">{m.target_label}</span> : <span className="text-slate-400 italic">not migrated</span>}
                        {m.date_format && m.date_format !== "iso" && <Badge className="ml-1.5" tone="sky">{DATE_FMT[m.date_format] ?? m.date_format}</Badge>}
                      </td>
                      <td className="px-2 py-2">{m.target_field || m.status === "pending" ? <ConfidenceBar value={m.confidence} /> : null}</td>
                      <td className="px-2 py-2"><Badge tone={method.tone}>{method.label}</Badge></td>
                      <td className="max-w-[260px] truncate px-2 py-2 font-mono text-xs text-slate-500">{m.samples.slice(0, 3).join(" · ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        );
      })}
    </div>
  );
}
