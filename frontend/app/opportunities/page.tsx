"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import {
  api,
  Opportunity,
  OPPORTUNITY_STATUS_LABELS,
  OpportunityInput,
} from "../../lib/api";
import { money, numberShort } from "../../lib/format";

export default function OpportunitiesPage() {
  const [rows, setRows] = useState<Opportunity[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setRows(await api.listOpportunities());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Opportunities</h1>
          <p className="text-sm text-gray-400">
            Trade mandates being worked — multiple suppliers and buyers per idea.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          + New opportunity
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="card">
        <table className="w-full text-sm">
          <thead className="table-head">
            <tr>
              <th className="py-2">Title</th>
              <th>Status</th>
              <th>Commodity</th>
              <th>Volume</th>
              <th>Destination</th>
              <th>Target band</th>
              <th>Incoterms</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id} className="row">
                <td className="py-2">
                  <Link
                    href={`/opportunities/${o.id}`}
                    className="text-accent hover:underline"
                  >
                    {o.title}
                  </Link>
                </td>
                <td>
                  <span className="badge">
                    {OPPORTUNITY_STATUS_LABELS[o.status] || o.status}
                  </span>
                </td>
                <td>{o.commodity}</td>
                <td>{numberShort(o.volume_mt)} MT</td>
                <td className="text-gray-300">
                  {[o.destination_port, o.destination_country]
                    .filter(Boolean)
                    .join(", ") || "—"}
                </td>
                <td className="text-gray-300">
                  {o.target_price_min != null && o.target_price_max != null
                    ? `${money(o.target_price_min, o.currency)} – ${money(
                        o.target_price_max,
                        o.currency,
                      )} / MT`
                    : "—"}
                </td>
                <td className="text-gray-400">{o.incoterms || "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="py-8 text-center text-gray-500">
                  No opportunities yet. Click &quot;+ New opportunity&quot; to
                  start.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {creating && (
        <NewOpportunityModal
          onClose={() => setCreating(false)}
          onCreated={async () => {
            setCreating(false);
            await refresh();
          }}
        />
      )}
    </AppShell>
  );
}

function NewOpportunityModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<OpportunityInput>({
    title: "",
    commodity: "sugar",
    volume_mt: 0,
    destination_country: "",
    destination_port: "",
    incoterms: "CFR",
    target_price_min: null,
    target_price_max: null,
    currency: "USD",
    notes: "",
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.createOpportunity(form);
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60">
      <form
        onSubmit={submit}
        className="card w-full max-w-lg space-y-3 p-5"
      >
        <h2 className="text-lg font-semibold">New opportunity</h2>
        <label className="block text-sm">
          <span className="text-gray-400">Title</span>
          <input
            className="input mt-1 w-full"
            required
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="Nigeria sugar 50k MT CFR Lagos"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Commodity</span>
            <input
              className="input mt-1 w-full"
              required
              value={form.commodity}
              onChange={(e) =>
                setForm({ ...form, commodity: e.target.value.toLowerCase() })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Volume (MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              min="0"
              value={form.volume_mt ?? 0}
              onChange={(e) =>
                setForm({ ...form, volume_mt: Number(e.target.value) })
              }
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Destination country</span>
            <input
              className="input mt-1 w-full"
              value={form.destination_country ?? ""}
              onChange={(e) =>
                setForm({ ...form, destination_country: e.target.value })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Destination port</span>
            <input
              className="input mt-1 w-full"
              value={form.destination_port ?? ""}
              onChange={(e) =>
                setForm({ ...form, destination_port: e.target.value })
              }
            />
          </label>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Incoterms</span>
            <select
              className="input mt-1 w-full"
              value={form.incoterms ?? "CFR"}
              onChange={(e) =>
                setForm({ ...form, incoterms: e.target.value })
              }
            >
              <option value="FOB">FOB</option>
              <option value="CFR">CFR</option>
              <option value="CIF">CIF</option>
              <option value="DAP">DAP</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Target min ($/MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              value={form.target_price_min ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  target_price_min: e.target.value
                    ? Number(e.target.value)
                    : null,
                })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Target max ($/MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              value={form.target_price_max ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  target_price_max: e.target.value
                    ? Number(e.target.value)
                    : null,
                })
              }
            />
          </label>
        </div>
        <label className="block text-sm">
          <span className="text-gray-400">Notes</span>
          <textarea
            className="input mt-1 w-full"
            rows={2}
            value={form.notes ?? ""}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
          />
        </label>
        {error && (
          <div className="text-sm text-red-400">{error}</div>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            className="btn-ghost"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <button className="btn-primary" disabled={submitting}>
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
