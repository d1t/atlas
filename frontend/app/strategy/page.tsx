"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { api, Strategy, StrategyInput } from "../../lib/api";
import { numberShort } from "../../lib/format";

export default function StrategyListPage() {
  const [rows, setRows] = useState<Strategy[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setRows(await api.listStrategies());
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
          <h1 className="text-2xl font-semibold">Strategy</h1>
          <p className="text-sm text-gray-400">
            North-star goals that steer the whole value chain — origination,
            demand, supply, and execution — down to a weekly and daily cadence.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          + New strategy
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {rows.map((s) => (
          <Link
            key={s.id}
            href={`/strategy/${s.id}`}
            className="card block transition hover:border-accent/50"
          >
            <div className="flex items-center justify-between">
              <div className="font-semibold text-accent">{s.title}</div>
              <span className="badge capitalize">{s.status}</span>
            </div>
            {s.north_star && (
              <p className="mt-1 text-sm text-gray-300">{s.north_star}</p>
            )}
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
              {s.commodity && <span>Commodity: {s.commodity}</span>}
              {s.origin_region && s.destination_region && (
                <span>
                  {s.origin_region} → {s.destination_region}
                </span>
              )}
              {s.target_volume_mt != null && (
                <span>{numberShort(s.target_volume_mt)} MT / {s.horizon}</span>
              )}
              {s.target_margin_per_mt != null && (
                <span>${s.target_margin_per_mt}/MT margin</span>
              )}
            </div>
          </Link>
        ))}
        {rows.length === 0 && (
          <div className="card text-center text-gray-500">
            No strategies yet. Click &quot;+ New strategy&quot; to define your
            big-picture goal and let Atlas build the cadence.
          </div>
        )}
      </div>

      {creating && (
        <NewStrategyModal
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

function NewStrategyModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<StrategyInput>({
    title: "",
    north_star: "",
    commodity: "sugar",
    origin_region: "",
    destination_region: "",
    horizon: "quarter",
    target_volume_mt: null,
    target_margin_per_mt: null,
    auto_plan: true,
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.createStrategy(form);
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4">
      <form onSubmit={submit} className="card w-full max-w-lg space-y-3 p-5">
        <h2 className="text-lg font-semibold">New strategy</h2>
        <label className="block text-sm">
          <span className="text-gray-400">Title</span>
          <input
            className="input mt-1 w-full"
            required
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="Control the Brazil → Nigeria sugar chain"
          />
        </label>
        <label className="block text-sm">
          <span className="text-gray-400">North-star goal</span>
          <textarea
            className="input mt-1 w-full"
            rows={2}
            value={form.north_star ?? ""}
            onChange={(e) => setForm({ ...form, north_star: e.target.value })}
            placeholder="Own the value chain: 50k MT/quarter at >$18/MT margin, 3 committed buyers."
          />
        </label>
        <div className="grid grid-cols-3 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Commodity</span>
            <input
              className="input mt-1 w-full"
              value={form.commodity ?? ""}
              onChange={(e) =>
                setForm({ ...form, commodity: e.target.value.toLowerCase() })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Origin region</span>
            <input
              className="input mt-1 w-full"
              value={form.origin_region ?? ""}
              onChange={(e) =>
                setForm({ ...form, origin_region: e.target.value })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Destination region</span>
            <input
              className="input mt-1 w-full"
              value={form.destination_region ?? ""}
              onChange={(e) =>
                setForm({ ...form, destination_region: e.target.value })
              }
            />
          </label>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Horizon</span>
            <select
              className="input mt-1 w-full"
              value={form.horizon ?? "quarter"}
              onChange={(e) => setForm({ ...form, horizon: e.target.value })}
            >
              <option value="month">Month</option>
              <option value="quarter">Quarter</option>
              <option value="year">Year</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Target volume (MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              value={form.target_volume_mt ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  target_volume_mt: e.target.value
                    ? Number(e.target.value)
                    : null,
                })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Target margin ($/MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              value={form.target_margin_per_mt ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  target_margin_per_mt: e.target.value
                    ? Number(e.target.value)
                    : null,
                })
              }
            />
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={form.auto_plan ?? true}
            onChange={(e) => setForm({ ...form, auto_plan: e.target.checked })}
          />
          Auto-draft the four-pillar objectives with AI
        </label>
        {error && <div className="text-sm text-red-400">{error}</div>}
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
