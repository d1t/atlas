"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { api, Deal, STAGE_LABELS, Supplier } from "../../lib/api";
import { money, numberShort } from "../../lib/format";

export default function DealsPage() {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [creating, setCreating] = useState(false);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);

  async function refresh() {
    setDeals(await api.listDeals());
  }

  useEffect(() => {
    refresh();
    api.listSuppliers().then(setSuppliers).catch(() => {});
  }, []);

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Deals</h1>
          <p className="text-sm text-gray-400">
            All open and closed trades across the desk.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          + New deal
        </button>
      </div>

      <div className="card">
        <table className="w-full text-sm">
          <thead className="table-head">
            <tr>
              <th className="py-2">Title</th>
              <th>Stage</th>
              <th>Commodity</th>
              <th>Volume</th>
              <th>Margin/MT</th>
              <th>Total margin</th>
              <th>Structure</th>
            </tr>
          </thead>
          <tbody>
            {deals.map((d) => (
              <tr key={d.id} className="row">
                <td className="py-2">
                  <Link
                    href={`/deals/${d.id}`}
                    className="text-accent hover:underline"
                  >
                    {d.title}
                  </Link>
                </td>
                <td>
                  <span className="badge">{STAGE_LABELS[d.stage] || d.stage}</span>
                </td>
                <td>{d.commodity}</td>
                <td>{numberShort(d.volume_mt)} MT</td>
                <td>{money(d.margin_per_mt, d.currency)}</td>
                <td>{money(d.total_margin, d.currency)}</td>
                <td className="text-gray-400">{d.structure || "—"}</td>
              </tr>
            ))}
            {deals.length === 0 && (
              <tr>
                <td colSpan={7} className="py-8 text-center text-gray-500">
                  No deals yet. Click "+ New deal" to start.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {creating && (
        <NewDealModal
          suppliers={suppliers}
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

function NewDealModal({
  suppliers,
  onClose,
  onCreated,
}: {
  suppliers: Supplier[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState({
    title: "",
    commodity: "",
    volume_mt: 0,
    buy_price: 0,
    sell_price: 0,
    freight_estimate: 0,
    incoterms: "FOB",
    supplier_id: "" as string,
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.createDeal({
        title: form.title,
        commodity: form.commodity,
        volume_mt: Number(form.volume_mt),
        buy_price: Number(form.buy_price),
        sell_price: Number(form.sell_price),
        freight_estimate: Number(form.freight_estimate),
        incoterms: form.incoterms || null,
        supplier_id: form.supplier_id ? Number(form.supplier_id) : null,
      });
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create deal");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <form
        onSubmit={submit}
        className="card w-full max-w-lg space-y-3"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">New deal</h2>
          <button type="button" onClick={onClose} className="text-gray-500">
            ✕
          </button>
        </div>

        <div>
          <label className="label">Title</label>
          <input
            className="input"
            required
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="Brazil Sugar 10,000 MT Q2"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Commodity</label>
            <input
              className="input"
              required
              value={form.commodity}
              onChange={(e) => setForm({ ...form, commodity: e.target.value })}
              placeholder="Sugar"
            />
          </div>
          <div>
            <label className="label">Incoterms</label>
            <select
              className="select"
              value={form.incoterms}
              onChange={(e) => setForm({ ...form, incoterms: e.target.value })}
            >
              <option>FOB</option>
              <option>CIF</option>
              <option>CFR</option>
              <option>FCA</option>
              <option>DAP</option>
            </select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <NumField
            label="Volume (MT)"
            value={form.volume_mt}
            onChange={(v) => setForm({ ...form, volume_mt: v })}
          />
          <NumField
            label="Freight / MT"
            value={form.freight_estimate}
            onChange={(v) => setForm({ ...form, freight_estimate: v })}
          />
          <NumField
            label="Buy price / MT"
            value={form.buy_price}
            onChange={(v) => setForm({ ...form, buy_price: v })}
          />
          <NumField
            label="Sell price / MT"
            value={form.sell_price}
            onChange={(v) => setForm({ ...form, sell_price: v })}
          />
        </div>
        <div>
          <label className="label">Supplier (optional)</label>
          <select
            className="select"
            value={form.supplier_id}
            onChange={(e) => setForm({ ...form, supplier_id: e.target.value })}
          >
            <option value="">—</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="btn-ghost">
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Create deal"}
          </button>
        </div>
      </form>
    </div>
  );
}

function NumField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <input
        className="input"
        type="number"
        min={0}
        step="0.01"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}
