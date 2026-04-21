"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../../components/AppShell";
import { PriceDisplay } from "../../../components/PriceDisplay";
import {
  Activity,
  api,
  Deal,
  Document,
  STAGE_LABELS,
  Scenario,
  Task,
} from "../../../lib/api";
import { money } from "../../../lib/format";

const STAGES = [
  "lead",
  "contacted",
  "qualified",
  "pricing",
  "buyer_matched",
  "spa",
  "lc",
  "shipment",
  "closed",
  "lost",
];

const DOC_TYPES = [
  { value: "outreach_email", label: "Outreach email (no price)" },
  { value: "counter_offer_email", label: "Counter-offer email (vs market)" },
  { value: "ncnda", label: "NCNDA" },
  { value: "loi", label: "LOI" },
  { value: "spa_buyer", label: "SPA (buyer)" },
  { value: "spa_supplier", label: "SPA (supplier)" },
  { value: "fpa", label: "FPA" },
  { value: "imfpa", label: "IMFPA" },
];

export default function DealWorkspacePage() {
  const params = useParams();
  const router = useRouter();
  const id = Number(params?.id);
  const [deal, setDeal] = useState<Deal | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [docs, setDocs] = useState<Document[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [docType, setDocType] = useState("outreach_email");
  const [generating, setGenerating] = useState(false);
  const [openDoc, setOpenDoc] = useState<Document | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [d, a, t, ds] = await Promise.all([
        api.getDeal(id),
        api.listActivity(id),
        api.listTasks(id),
        api.listDocuments({ deal_id: id }),
      ]);
      setDeal(d);
      setActivity(a);
      setTasks(t);
      setDocs(ds);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load deal");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function updateNumbers(patch: Partial<Deal>) {
    if (!deal) return;
    const updated = await api.updateDeal(deal.id, patch);
    setDeal(updated);
  }

  async function move(stage: string) {
    if (!deal) return;
    try {
      const updated = await api.changeStage(deal.id, stage);
      setDeal(updated);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid transition");
    }
  }

  async function addNote() {
    if (!deal || !note.trim()) return;
    await api.addActivity(deal.id, note.trim());
    setNote("");
    setActivity(await api.listActivity(deal.id));
  }

  async function addTask() {
    if (!deal || !taskTitle.trim()) return;
    await api.addTask(deal.id, taskTitle.trim());
    setTaskTitle("");
    setTasks(await api.listTasks(deal.id));
  }

  async function toggleTask(t: Task) {
    await api.toggleTask(t.id, !t.done);
    setTasks(await api.listTasks(deal!.id));
  }

  async function generateDoc() {
    if (!deal) return;
    setGenerating(true);
    try {
      const doc = await api.generateDocument({ type: docType, deal_id: deal.id });
      setDocs([doc, ...docs]);
      setOpenDoc(doc);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  }

  if (error && !deal) {
    return (
      <AppShell>
        <div className="card border-danger text-danger">{error}</div>
      </AppShell>
    );
  }

  if (!deal) {
    return (
      <AppShell>
        <p className="text-gray-400">Loading…</p>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mb-4 flex items-start justify-between">
        <div>
          <button
            onClick={() => router.push("/deals")}
            className="mb-2 text-xs text-gray-500 hover:text-gray-300"
          >
            ← Back to deals
          </button>
          <h1 className="text-2xl font-semibold">{deal.title}</h1>
          <p className="text-sm text-gray-400">
            {deal.commodity} · {deal.incoterms || "—"} · {deal.currency}
          </p>
        </div>
        <span className="badge text-base">
          {STAGE_LABELS[deal.stage] || deal.stage}
        </span>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {STAGES.map((s) => (
          <button
            key={s}
            onClick={() => move(s)}
            className={`btn ${
              s === deal.stage ? "bg-accent text-white" : "bg-surface2 text-gray-300"
            }`}
          >
            {STAGE_LABELS[s] || s}
          </button>
        ))}
      </div>

      {error && (
        <div className="card mb-4 border-danger text-sm text-danger">{error}</div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold">Pricing & structure</h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Num
              label="Volume (MT)"
              value={deal.volume_mt}
              onBlur={(v) => updateNumbers({ volume_mt: v })}
            />
            <Num
              label="Buy / MT"
              value={deal.buy_price}
              onBlur={(v) => updateNumbers({ buy_price: v })}
            />
            <Num
              label="Sell / MT"
              value={deal.sell_price}
              onBlur={(v) => updateNumbers({ sell_price: v })}
            />
            <Num
              label="Freight / MT"
              value={deal.freight_estimate}
              onBlur={(v) => updateNumbers({ freight_estimate: v })}
            />
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
            <Stat label="Margin / MT" value={money(deal.margin_per_mt, deal.currency)} />
            <Stat label="Total value" value={money(deal.total_value, deal.currency)} />
            <Stat label="Total margin" value={money(deal.total_margin, deal.currency)} />
          </div>

          <div className="mt-4 rounded-md bg-surface2 p-3">
            <div className="text-xs uppercase text-gray-500">Recommended structure</div>
            <div className="mt-1 text-lg font-semibold text-accent">
              {deal.structure || "—"}
            </div>
            <p className="mt-1 text-xs text-gray-300">
              {deal.metrics?.rationale || ""}
            </p>
          </div>

          <div className="mt-4">
            <div className="mb-2 text-xs uppercase text-gray-500">
              Market reference
            </div>
            <PriceDisplay
              commodity={deal.commodity}
              buyPrice={deal.buy_price}
              sellPrice={deal.sell_price}
            />
          </div>

          {deal.metrics?.scenarios && (
            <div className="mt-4">
              <div className="mb-2 text-xs uppercase text-gray-500">
                Scenarios
              </div>
              <table className="w-full text-sm">
                <thead className="table-head">
                  <tr>
                    <th className="py-1">Scenario</th>
                    <th>Sell/MT</th>
                    <th>Freight/MT</th>
                    <th>Margin/MT</th>
                    <th>Total margin</th>
                  </tr>
                </thead>
                <tbody>
                  {deal.metrics.scenarios.map((s: Scenario) => (
                    <tr key={s.name} className="row">
                      <td className="py-1 capitalize">{s.name}</td>
                      <td>{money(s.sell_price, deal.currency)}</td>
                      <td>{money(s.freight, deal.currency)}</td>
                      <td
                        className={
                          s.margin_per_mt < 0 ? "text-danger" : "text-success"
                        }
                      >
                        {money(s.margin_per_mt, deal.currency)}
                      </td>
                      <td>{money(s.total_margin, deal.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <h2 className="mb-3 text-sm font-semibold">Documents</h2>
          <div className="flex gap-2">
            <select
              className="select"
              value={docType}
              onChange={(e) => setDocType(e.target.value)}
            >
              {DOC_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
            <button
              onClick={generateDoc}
              className="btn-primary"
              disabled={generating}
            >
              {generating ? "…" : "Generate"}
            </button>
          </div>

          <ul className="mt-3 space-y-2 text-sm">
            {docs.map((d) => (
              <li
                key={d.id}
                className="flex items-center justify-between rounded-md bg-surface2 px-3 py-2"
              >
                <button
                  className="text-left text-accent hover:underline"
                  onClick={() => setOpenDoc(d)}
                >
                  {d.title}
                </button>
                <div className="flex gap-2 text-xs">
                  <a
                    className="text-gray-400 hover:text-gray-200"
                    href={api.documentMarkdownUrl(d.id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    md
                  </a>
                  <a
                    className="text-gray-400 hover:text-gray-200"
                    href={api.documentDocxUrl(d.id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    docx
                  </a>
                </div>
              </li>
            ))}
            {docs.length === 0 && (
              <li className="py-4 text-center text-xs text-gray-500">
                No documents yet.
              </li>
            )}
          </ul>
        </div>

        <div className="card">
          <h2 className="mb-3 text-sm font-semibold">Tasks</h2>
          <div className="flex gap-2">
            <input
              className="input"
              placeholder="New task…"
              value={taskTitle}
              onChange={(e) => setTaskTitle(e.target.value)}
            />
            <button onClick={addTask} className="btn-primary">
              Add
            </button>
          </div>
          <ul className="mt-3 space-y-2 text-sm">
            {tasks.map((t) => (
              <li
                key={t.id}
                className="flex items-center gap-2 rounded-md bg-surface2 px-3 py-2"
              >
                <input
                  type="checkbox"
                  checked={t.done}
                  onChange={() => toggleTask(t)}
                />
                <span className={t.done ? "line-through text-gray-500" : ""}>
                  {t.title}
                </span>
              </li>
            ))}
            {tasks.length === 0 && (
              <li className="py-4 text-center text-xs text-gray-500">
                No tasks yet.
              </li>
            )}
          </ul>
        </div>

        <div className="card lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold">Activity log</h2>
          <div className="flex gap-2">
            <input
              className="input"
              placeholder="Add note…"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addNote()}
            />
            <button onClick={addNote} className="btn-primary">
              Log
            </button>
          </div>
          <ul className="mt-3 space-y-2 text-sm">
            {activity.map((a) => (
              <li
                key={a.id}
                className="rounded-md bg-surface2 px-3 py-2"
              >
                <div className="flex items-center justify-between text-xs text-gray-500">
                  <span className="uppercase">{a.type.replace(/_/g, " ")}</span>
                  <time>{new Date(a.created_at).toLocaleString()}</time>
                </div>
                <p className="mt-1 text-gray-200">{a.message}</p>
              </li>
            ))}
            {activity.length === 0 && (
              <li className="py-4 text-center text-xs text-gray-500">
                No activity yet.
              </li>
            )}
          </ul>
        </div>
      </div>

      {openDoc && <DocumentModal doc={openDoc} onClose={() => setOpenDoc(null)} onSaved={load} />}
    </AppShell>
  );
}

function Num({
  label,
  value,
  onBlur,
}: {
  label: string;
  value: number;
  onBlur: (v: number) => void;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <div>
      <label className="label">{label}</label>
      <input
        className="input"
        type="number"
        min={0}
        step="0.01"
        value={local}
        onChange={(e) => setLocal(Number(e.target.value))}
        onBlur={() => local !== value && onBlur(local)}
      />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-surface2 p-3">
      <div className="text-xs uppercase text-gray-500">{label}</div>
      <div className="mt-0.5 text-lg font-semibold text-gray-100">{value}</div>
    </div>
  );
}

function DocumentModal({
  doc,
  onClose,
  onSaved,
}: {
  doc: Document;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [content, setContent] = useState(doc.content);
  const [title, setTitle] = useState(doc.title);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await api.updateDocument(doc.id, { title, content });
      await onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="card flex h-[85vh] w-full max-w-3xl flex-col">
        <div className="mb-3 flex items-center justify-between gap-3">
          <input
            className="input text-lg"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button onClick={onClose} className="text-gray-500">
            ✕
          </button>
        </div>
        <textarea
          className="textarea flex-1 font-mono text-xs"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div className="mt-3 flex justify-end gap-2">
          <a
            href={api.documentMarkdownUrl(doc.id)}
            className="btn-ghost"
            target="_blank"
            rel="noreferrer"
          >
            Download .md
          </a>
          <a
            href={api.documentDocxUrl(doc.id)}
            className="btn-ghost"
            target="_blank"
            rel="noreferrer"
          >
            Download .docx
          </a>
          <button onClick={save} className="btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
