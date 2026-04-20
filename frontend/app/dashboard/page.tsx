"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { api, Deal, PipelineStats, STAGE_LABELS } from "../../lib/api";
import { money, numberShort } from "../../lib/format";

export default function DashboardPage() {
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [active, setActive] = useState<Deal[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.pipelineStats(), api.listDeals()])
      .then(([s, deals]) => {
        setStats(s);
        setActive(
          deals
            .filter((d) => !["closed", "lost"].includes(d.stage))
            .slice(0, 10),
        );
      })
      .catch((e) => setError(e.message));
  }, []);

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-gray-400">
          Portfolio of active commodity deals.
        </p>
      </div>

      {error && <div className="card mb-4 border-danger text-danger">{error}</div>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Stat
          label="Active deals"
          value={String(stats?.total_deals ?? "—")}
        />
        <Stat
          label="Pipeline value"
          value={stats ? money(stats.total_value) : "—"}
        />
        <Stat
          label="Potential margin"
          value={stats ? money(stats.total_margin) : "—"}
        />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card lg:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Active deals</h2>
            <Link href="/deals" className="text-xs text-accent">
              View all →
            </Link>
          </div>
          {active.length === 0 ? (
            <div className="py-8 text-center text-sm text-gray-500">
              No active deals yet.{" "}
              <Link href="/deals" className="text-accent">
                Create one
              </Link>
              .
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="table-head">
                <tr>
                  <th className="py-2">Title</th>
                  <th>Stage</th>
                  <th>Volume</th>
                  <th>Margin/MT</th>
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {active.map((d) => (
                  <tr key={d.id} className="row">
                    <td className="py-2">
                      <Link
                        href={`/deals/${d.id}`}
                        className="text-accent hover:underline"
                      >
                        {d.title}
                      </Link>
                      <div className="text-xs text-gray-500">
                        {d.commodity} · {d.incoterms || "—"}
                      </div>
                    </td>
                    <td>
                      <span className="badge">
                        {STAGE_LABELS[d.stage] || d.stage}
                      </span>
                    </td>
                    <td>{numberShort(d.volume_mt)} MT</td>
                    <td>{money(d.margin_per_mt, d.currency)}</td>
                    <td>{money(d.total_value, d.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h2 className="mb-3 text-sm font-semibold">Pipeline by stage</h2>
          <div className="space-y-2">
            {stats
              ? Object.entries(stats.by_stage).map(([stage, v]) => (
                  <div key={stage} className="flex items-center justify-between text-sm">
                    <span className="text-gray-300">
                      {STAGE_LABELS[stage] || stage}
                    </span>
                    <span className="text-gray-400">
                      {v.count} · {money(v.value)}
                    </span>
                  </div>
                ))
              : <p className="text-sm text-gray-500">Loading…</p>}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-gray-100">{value}</div>
    </div>
  );
}
