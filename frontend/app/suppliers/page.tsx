"use client";

import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { api, Supplier } from "../../lib/api";

export default function SuppliersPage() {
  const [q, setQ] = useState("");
  const [country, setCountry] = useState("");
  const [commodity, setCommodity] = useState("");
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [selected, setSelected] = useState<Supplier | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [classifyResult, setClassifyResult] = useState<{
    type: string;
    confidence: number;
    reasoning: string;
    supplierId: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const results = await api.listSuppliers({
        q: q || undefined,
        country: country || undefined,
        commodity: commodity || undefined,
      });
      setSuppliers(results);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function discover() {
    if (!commodity) {
      setError("Enter a commodity to discover suppliers");
      return;
    }
    setDiscovering(true);
    try {
      await api.discoverSuppliers(commodity, country || undefined, 8);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Discovery failed");
    } finally {
      setDiscovering(false);
    }
  }

  function selectSupplier(s: Supplier | null) {
    setSelected(s);
    // Stale classification belongs to a different supplier; clear it.
    setClassifyResult(null);
  }

  async function classify(id: number) {
    setClassifying(true);
    setClassifyResult(null);
    setError(null);
    try {
      const result = await api.classifySupplier(id);
      await refresh();
      // Guard every post-await state mutation against the user having
      // navigated to a different supplier in the meantime. `selected`
      // here is the live value (setSelected is only called below when
      // the id still matches), so this stale-closure check is safe.
      setSelected((current) => {
        if (current?.id !== id) return current;
        setClassifyResult({ ...result, supplierId: id });
        api.getSupplier(id).then((fresh) => {
          setSelected((c) => (c?.id === id ? fresh : c));
        });
        return current;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Classification failed");
    } finally {
      setClassifying(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Suppliers</h1>
          <p className="text-sm text-gray-400">
            Search, discover and classify counterparties.
          </p>
        </div>
      </div>

      <div className="card mb-4">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <div>
            <label className="label">Name / keyword</label>
            <input
              className="input"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search…"
            />
          </div>
          <div>
            <label className="label">Country</label>
            <input
              className="input"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              placeholder="Brazil"
            />
          </div>
          <div>
            <label className="label">Commodity</label>
            <input
              className="input"
              value={commodity}
              onChange={(e) => setCommodity(e.target.value)}
              placeholder="Sugar"
            />
          </div>
          <div className="flex items-end gap-2">
            <button className="btn-ghost flex-1" onClick={refresh}>
              Filter
            </button>
            <button
              className="btn-primary flex-1"
              onClick={discover}
              disabled={discovering}
            >
              {discovering ? "Discovering…" : "AI Discover"}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="card mb-4 border-danger text-sm text-danger">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card lg:col-span-2">
          <table className="w-full text-sm">
            <thead className="table-head">
              <tr>
                <th className="py-2">Name</th>
                <th>Type</th>
                <th>Country</th>
                <th>Commodity</th>
                <th>Credibility</th>
                <th>Risk</th>
              </tr>
            </thead>
            <tbody>
              {suppliers.map((s) => (
                <tr
                  key={s.id}
                  className="row cursor-pointer"
                  onClick={() => selectSupplier(s)}
                >
                  <td className="py-2 font-medium text-gray-100">{s.name}</td>
                  <td>
                    <span className="badge">{s.type || "unknown"}</span>
                  </td>
                  <td>{s.country || "—"}</td>
                  <td>{s.commodity || "—"}</td>
                  <td className="text-success">{s.credibility_score}</td>
                  <td className={s.risk_score > 65 ? "text-danger" : "text-warning"}>
                    {s.risk_score}
                  </td>
                </tr>
              ))}
              {suppliers.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-gray-500">
                    No suppliers. Try AI Discover with a commodity.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="card">
          {selected ? (
            <div>
              <div className="mb-3 flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-semibold">{selected.name}</h2>
                  <div className="text-xs text-gray-500">
                    {selected.type || "unknown"} · {selected.country || "—"}
                  </div>
                </div>
                <button
                  className="btn-ghost"
                  onClick={() => classify(selected.id)}
                  disabled={classifying}
                >
                  {classifying ? "Classifying…" : "AI classify"}
                </button>
              </div>

              <dl className="space-y-2 text-sm">
                <Row k="Website" v={selected.website} isLink />
                <Row k="Email" v={selected.email} />
                <Row k="Phone" v={selected.phone} />
                <Row k="Contact" v={selected.contact_name} />
                <Row k="Commodity" v={selected.commodity} />
                <Row k="Source" v={selected.source} />
              </dl>

              {selected.description && (
                <p className="mt-4 rounded-md bg-surface2 p-3 text-xs text-gray-300">
                  {selected.description}
                </p>
              )}

              <div className="mt-4 grid grid-cols-2 gap-2 text-center">
                <div className="rounded-md bg-surface2 p-2">
                  <div className="text-xs text-gray-400">Credibility</div>
                  <div className="text-lg font-semibold text-success">
                    {selected.credibility_score}
                  </div>
                </div>
                <div className="rounded-md bg-surface2 p-2">
                  <div className="text-xs text-gray-400">Risk</div>
                  <div
                    className={`text-lg font-semibold ${
                      selected.risk_score > 65 ? "text-danger" : "text-warning"
                    }`}
                  >
                    {selected.risk_score}
                  </div>
                </div>
              </div>

              {classifyResult && selected && classifyResult.supplierId === selected.id && (
                <div className="mt-3 rounded-md border border-accent/30 bg-accent/10 p-3 text-xs">
                  <div className="mb-1 font-medium text-accent">
                    Classified as <span className="uppercase">{classifyResult.type}</span>
                    {" "}· confidence {(classifyResult.confidence * 100).toFixed(0)}%
                  </div>
                  {classifyResult.reasoning && (
                    <div className="text-gray-300">{classifyResult.reasoning}</div>
                  )}
                </div>
              )}

              {selected.red_flags.length > 0 && (
                <div className="mt-3">
                  <div className="text-xs font-medium uppercase text-danger">
                    Red flags
                  </div>
                  <ul className="mt-1 space-y-1 text-xs text-gray-300">
                    {selected.red_flags.map((f) => (
                      <li key={f}>• {f.replace(/_/g, " ")}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-gray-500">
              Select a supplier to view details.
            </p>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function Row({ k, v, isLink }: { k: string; v: string | null | undefined; isLink?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-xs uppercase tracking-wide text-gray-500">{k}</dt>
      <dd className="truncate text-right text-gray-300">
        {v ? (
          isLink ? (
            <a className="text-accent hover:underline" href={v} target="_blank" rel="noreferrer">
              {v}
            </a>
          ) : (
            v
          )
        ) : (
          "—"
        )}
      </dd>
    </div>
  );
}
