"use client";

import { useParams, useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { AppShell } from "../../../components/AppShell";
import {
  api,
  BuyerLead,
  BuyerLeadInput,
  HealthScore,
  MatchPair,
  MatchingResult,
  NextAction,
  NextActionsOut,
  Opportunity,
  OPPORTUNITY_STATUS_LABELS,
  OpportunityDashboard,
  SupplierLead,
  SupplierLeadInput,
} from "../../../lib/api";
import { classNames, money, numberShort } from "../../../lib/format";

export default function OpportunityWorkspacePage() {
  const params = useParams();
  const router = useRouter();
  const id = Number(params?.id);

  const [data, setData] = useState<OpportunityDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addingSupplier, setAddingSupplier] = useState(false);
  const [addingBuyer, setAddingBuyer] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const d = await api.getOpportunityDashboard(id);
      setData(d);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (!data) {
    return (
      <AppShell>
        <div className="text-sm text-gray-400">
          {error ? `Error: ${error}` : "Loading…"}
        </div>
      </AppShell>
    );
  }

  const {
    opportunity: opp,
    supplier_leads: suppliers,
    buyer_leads: buyers,
    matches,
    health,
    next_actions: nextActions,
  } = data;

  async function onPromote(pair: MatchPair) {
    if (!confirm(`Promote this pair to a Deal? This will create a Deal row.`)) {
      return;
    }
    try {
      const deal = await api.promoteMatchToDeal(id, {
        supplier_lead_id: pair.supplier_lead_id,
        buyer_lead_id: pair.buyer_lead_id,
      });
      router.push(`/deals/${deal.deal_id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function onStatusChange(status: string) {
    try {
      await api.updateOpportunity(id, { status });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <AppShell>
      <OpportunityHeader
        opp={opp}
        health={health}
        onStatusChange={onStatusChange}
      />

      {error && (
        <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <NextActionsPanel actions={nextActions} />
        <HealthPanel health={health} />
        <TargetBandPanel opp={opp} />
      </div>

      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SupplierPanel
          opportunityId={id}
          supplierLeads={suppliers}
          onChange={load}
          onAdd={() => setAddingSupplier(true)}
        />
        <BuyerPanel
          opportunityId={id}
          buyerLeads={buyers}
          onChange={load}
          onAdd={() => setAddingBuyer(true)}
        />
      </div>

      <div className="mt-5">
        <MatchesPanel matches={matches} onPromote={onPromote} />
      </div>

      {addingSupplier && (
        <NewSupplierLeadModal
          opportunityId={id}
          onClose={() => setAddingSupplier(false)}
          onCreated={async () => {
            setAddingSupplier(false);
            await load();
          }}
        />
      )}
      {addingBuyer && (
        <NewBuyerLeadModal
          opportunityId={id}
          defaultTargetPrice={opp.target_price_max ?? opp.target_price_min ?? null}
          defaultVolume={opp.volume_mt}
          onClose={() => setAddingBuyer(false)}
          onCreated={async () => {
            setAddingBuyer(false);
            await load();
          }}
        />
      )}
    </AppShell>
  );
}

// --- header -----------------------------------------------------------------

const STATUS_OPTIONS = [
  "draft",
  "sourcing",
  "negotiating",
  "matched",
  "closed",
  "lost",
];

function OpportunityHeader({
  opp,
  health,
  onStatusChange,
}: {
  opp: Opportunity;
  health: HealthScore;
  onStatusChange: (s: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="text-xs uppercase tracking-wide text-gray-500">
          Opportunity #{opp.id}
        </div>
        <h1 className="text-2xl font-semibold">{opp.title}</h1>
        <div className="mt-1 text-sm text-gray-400">
          {opp.commodity} · {numberShort(opp.volume_mt)} MT ·{" "}
          {[opp.destination_port, opp.destination_country]
            .filter(Boolean)
            .join(", ") || "—"}{" "}
          · {opp.incoterms || "—"}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <HealthBadge score={health.score} status={health.status} />
        <select
          className="input text-sm"
          value={opp.status}
          onChange={(e) => onStatusChange(e.target.value)}
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {OPPORTUNITY_STATUS_LABELS[s] || s}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function HealthBadge({ score, status }: { score: number; status: string }) {
  const tone =
    score >= 70
      ? "bg-emerald-500/20 text-emerald-300"
      : score >= 40
      ? "bg-amber-500/20 text-amber-300"
      : "bg-red-500/20 text-red-300";
  return (
    <div
      className={classNames(
        "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium",
        tone,
      )}
    >
      <span className="text-lg leading-none">{score}</span>
      <span className="text-xs uppercase tracking-wide">{status}</span>
    </div>
  );
}

// --- next actions -----------------------------------------------------------

function NextActionsPanel({ actions }: { actions: NextActionsOut }) {
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Next actions</h2>
        <span className="text-xs text-gray-500">
          {actions.actions.length} item{actions.actions.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="space-y-2 text-sm">
        {actions.actions.map((a, i) => (
          <ActionRow key={i} action={a} />
        ))}
        {actions.actions.length === 0 && (
          <li className="text-gray-500">Nothing urgent.</li>
        )}
      </ul>
    </div>
  );
}

function ActionRow({ action }: { action: NextAction }) {
  const tone =
    action.priority === "high"
      ? "bg-red-500/15 text-red-300 border-red-500/30"
      : action.priority === "medium"
      ? "bg-amber-500/10 text-amber-300 border-amber-500/30"
      : "bg-surface2 text-gray-300 border-border";
  return (
    <li className={classNames("rounded-md border px-3 py-2", tone)}>
      <div className="font-medium">{action.action}</div>
      <div className="text-xs opacity-80">{action.reasoning}</div>
    </li>
  );
}

// --- health breakdown -------------------------------------------------------

function HealthPanel({ health }: { health: HealthScore }) {
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Deal health breakdown</h2>
        <span className="text-xs text-gray-500">{health.status}</span>
      </div>
      <div className="mb-3 text-xs text-gray-400">{health.recommendation}</div>
      <ul className="space-y-2 text-xs">
        {health.factors.map((f) => (
          <li key={f.name}>
            <div className="flex items-center justify-between">
              <span className="text-gray-300">
                {f.name}{" "}
                <span className="text-gray-500">
                  ({Math.round(f.weight * 100)}%)
                </span>
              </span>
              <span className="tabular-nums text-gray-400">
                {Math.round(f.contribution)}
              </span>
            </div>
            <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-surface2">
              <div
                className="h-full bg-accent"
                style={{ width: `${Math.max(0, Math.min(1, f.value)) * 100}%` }}
              />
            </div>
            <div className="mt-0.5 text-[11px] text-gray-500">{f.detail}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TargetBandPanel({ opp }: { opp: Opportunity }) {
  return (
    <div className="card p-4 text-sm">
      <h2 className="mb-2 text-sm font-semibold">Target band</h2>
      <dl className="space-y-1.5 text-xs">
        <Row label="Min">
          {opp.target_price_min != null
            ? `${money(opp.target_price_min, opp.currency)} / MT`
            : "—"}
        </Row>
        <Row label="Max">
          {opp.target_price_max != null
            ? `${money(opp.target_price_max, opp.currency)} / MT`
            : "—"}
        </Row>
        <Row label="Incoterms">{opp.incoterms || "—"}</Row>
        <Row label="Volume">{numberShort(opp.volume_mt)} MT</Row>
        <Row label="Notes">{opp.notes || "—"}</Row>
      </dl>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-gray-500">{label}</dt>
      <dd className="text-right text-gray-300">{children}</dd>
    </div>
  );
}

// --- supplier panel ---------------------------------------------------------

const SUP_STATUSES = [
  "new",
  "contacted",
  "quoted",
  "shortlisted",
  "declined",
  "lost",
];

function SupplierPanel({
  opportunityId,
  supplierLeads,
  onChange,
  onAdd,
}: {
  opportunityId: number;
  supplierLeads: SupplierLead[];
  onChange: () => void;
  onAdd: () => void;
}) {
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          Supplier leads ({supplierLeads.length})
        </h2>
        <button className="btn-ghost text-xs" onClick={onAdd}>
          + Add supplier
        </button>
      </div>
      <table className="w-full text-xs">
        <thead className="text-left text-gray-500">
          <tr>
            <th className="pb-1.5 font-medium">Name / country</th>
            <th className="font-medium">Price</th>
            <th className="font-medium">Credibility</th>
            <th className="font-medium">Response</th>
            <th className="font-medium">Status</th>
            <th className="font-medium">Last contact</th>
          </tr>
        </thead>
        <tbody>
          {supplierLeads.map((s) => (
            <SupplierRow
              key={s.id}
              opportunityId={opportunityId}
              lead={s}
              onChange={onChange}
            />
          ))}
          {supplierLeads.length === 0 && (
            <tr>
              <td colSpan={6} className="py-6 text-center text-gray-500">
                No supplier leads yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function SupplierRow({
  opportunityId,
  lead,
  onChange,
}: {
  opportunityId: number;
  lead: SupplierLead;
  onChange: () => void;
}) {
  async function updateStatus(status: string) {
    await api.updateSupplierLead(opportunityId, lead.id, { status });
    onChange();
  }
  async function markContacted() {
    await api.updateSupplierLead(opportunityId, lead.id, {
      last_contacted_at: new Date().toISOString(),
      status: lead.status === "new" ? "contacted" : lead.status,
    });
    onChange();
  }
  async function remove() {
    if (!confirm(`Remove ${lead.supplier_name || "supplier"}?`)) return;
    await api.deleteSupplierLead(opportunityId, lead.id);
    onChange();
  }
  return (
    <tr className="row">
      <td className="py-1.5">
        <div className="text-gray-200">{lead.supplier_name || "—"}</div>
        <div className="text-[11px] text-gray-500">{lead.country || "—"}</div>
      </td>
      <td className="text-gray-300">
        {lead.price_mt != null ? `$${lead.price_mt.toFixed(0)}` : "—"}
        {lead.quoted_incoterms && (
          <span className="text-[11px] text-gray-500">
            {" "}
            {lead.quoted_incoterms}
          </span>
        )}
      </td>
      <td className="text-gray-300">{lead.credibility_score}</td>
      <td className="text-gray-300">{lead.responsiveness_score}</td>
      <td>
        <select
          className="input text-xs"
          value={lead.status}
          onChange={(e) => updateStatus(e.target.value)}
        >
          {SUP_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </td>
      <td className="text-gray-400">
        <div>
          {lead.last_contacted_at
            ? new Date(lead.last_contacted_at).toLocaleDateString()
            : "—"}
        </div>
        <div className="mt-0.5 flex gap-2 text-[11px]">
          <button className="text-accent hover:underline" onClick={markContacted}>
            log contact
          </button>
          <button className="text-red-400 hover:underline" onClick={remove}>
            remove
          </button>
        </div>
      </td>
    </tr>
  );
}

// --- buyer panel ------------------------------------------------------------

const BUY_STATUSES = [
  "new",
  "contacted",
  "engaged",
  "committed",
  "declined",
  "lost",
];

function BuyerPanel({
  opportunityId,
  buyerLeads,
  onChange,
  onAdd,
}: {
  opportunityId: number;
  buyerLeads: BuyerLead[];
  onChange: () => void;
  onAdd: () => void;
}) {
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          Buyer leads ({buyerLeads.length})
        </h2>
        <button className="btn-ghost text-xs" onClick={onAdd}>
          + Add buyer
        </button>
      </div>
      <table className="w-full text-xs">
        <thead className="text-left text-gray-500">
          <tr>
            <th className="pb-1.5 font-medium">Name / country</th>
            <th className="font-medium">Target</th>
            <th className="font-medium">Appetite</th>
            <th className="font-medium">Urgency</th>
            <th className="font-medium">Status</th>
            <th className="font-medium">Last contact</th>
          </tr>
        </thead>
        <tbody>
          {buyerLeads.map((b) => (
            <BuyerRow
              key={b.id}
              opportunityId={opportunityId}
              lead={b}
              onChange={onChange}
            />
          ))}
          {buyerLeads.length === 0 && (
            <tr>
              <td colSpan={6} className="py-6 text-center text-gray-500">
                No buyer leads yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function BuyerRow({
  opportunityId,
  lead,
  onChange,
}: {
  opportunityId: number;
  lead: BuyerLead;
  onChange: () => void;
}) {
  async function updateStatus(status: string) {
    await api.updateBuyerLead(opportunityId, lead.id, { status });
    onChange();
  }
  async function markContacted() {
    await api.updateBuyerLead(opportunityId, lead.id, {
      last_contacted_at: new Date().toISOString(),
      status: lead.status === "new" ? "contacted" : lead.status,
    });
    onChange();
  }
  async function remove() {
    if (!confirm(`Remove ${lead.buyer_name || "buyer"}?`)) return;
    await api.deleteBuyerLead(opportunityId, lead.id);
    onChange();
  }
  return (
    <tr className="row">
      <td className="py-1.5">
        <div className="text-gray-200">{lead.buyer_name || "—"}</div>
        <div className="text-[11px] text-gray-500">{lead.country || "—"}</div>
      </td>
      <td className="text-gray-300">
        {lead.target_price_mt != null
          ? `$${lead.target_price_mt.toFixed(0)}`
          : "—"}
      </td>
      <td className="text-gray-300 capitalize">{lead.appetite}</td>
      <td className="text-gray-300 capitalize">{lead.urgency}</td>
      <td>
        <select
          className="input text-xs"
          value={lead.status}
          onChange={(e) => updateStatus(e.target.value)}
        >
          {BUY_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </td>
      <td className="text-gray-400">
        <div>
          {lead.last_contacted_at
            ? new Date(lead.last_contacted_at).toLocaleDateString()
            : "—"}
        </div>
        <div className="mt-0.5 flex gap-2 text-[11px]">
          <button className="text-accent hover:underline" onClick={markContacted}>
            log contact
          </button>
          <button className="text-red-400 hover:underline" onClick={remove}>
            remove
          </button>
        </div>
      </td>
    </tr>
  );
}

// --- matches ----------------------------------------------------------------

function MatchesPanel({
  matches,
  onPromote,
}: {
  matches: MatchingResult;
  onPromote: (pair: MatchPair) => void;
}) {
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          Supplier × Buyer matches ({matches.viable_pairs}/{matches.total_pairs}{" "}
          viable)
        </h2>
        <div className="text-xs text-gray-500">
          Ranked by margin, credibility, responsiveness, alignment
        </div>
      </div>
      <table className="w-full text-xs">
        <thead className="text-left text-gray-500">
          <tr>
            <th className="pb-1.5 font-medium">Score</th>
            <th className="font-medium">Supplier</th>
            <th className="font-medium">Buyer</th>
            <th className="font-medium">Buy</th>
            <th className="font-medium">Sell</th>
            <th className="font-medium">Margin/MT</th>
            <th className="font-medium">Total margin</th>
            <th className="font-medium">Rationale</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {matches.pairs.map((p) => (
            <tr key={`${p.supplier_lead_id}-${p.buyer_lead_id}`} className="row">
              <td className="py-1.5">
                <ScoreBadge score={p.score} />
              </td>
              <td className="text-gray-300">{p.supplier_name || "—"}</td>
              <td className="text-gray-300">{p.buyer_name || "—"}</td>
              <td className="text-gray-400">
                {p.supplier_price_mt != null
                  ? `$${p.supplier_price_mt.toFixed(0)}`
                  : "—"}
              </td>
              <td className="text-gray-400">
                {p.buyer_target_price_mt != null
                  ? `$${p.buyer_target_price_mt.toFixed(0)}`
                  : "—"}
              </td>
              <td
                className={classNames(
                  p.margin_per_mt > 0 ? "text-emerald-300" : "text-red-400",
                )}
              >
                {p.margin_per_mt !== 0 ? `$${p.margin_per_mt.toFixed(0)}` : "—"}
              </td>
              <td className="text-gray-300">
                {p.total_margin != null && p.total_margin !== 0
                  ? money(p.total_margin)
                  : "—"}
              </td>
              <td className="max-w-[260px] text-[11px] text-gray-500">
                {p.reasoning.slice(0, 2).join(" · ")}
              </td>
              <td>
                <button
                  className="btn-primary text-xs"
                  onClick={() => onPromote(p)}
                  disabled={p.margin_per_mt <= 0 || p.score <= 0}
                >
                  Promote
                </button>
              </td>
            </tr>
          ))}
          {matches.pairs.length === 0 && (
            <tr>
              <td colSpan={9} className="py-6 text-center text-gray-500">
                Add supplier + buyer leads to see matches.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ScoreBadge({ score }: { score: number }) {
  const tone =
    score >= 70
      ? "bg-emerald-500/20 text-emerald-300"
      : score >= 40
      ? "bg-amber-500/20 text-amber-300"
      : "bg-surface2 text-gray-400";
  return (
    <span
      className={classNames(
        "rounded-md px-2 py-0.5 font-semibold tabular-nums",
        tone,
      )}
    >
      {score.toFixed(0)}
    </span>
  );
}

// --- modals -----------------------------------------------------------------

function NewSupplierLeadModal({
  opportunityId,
  onClose,
  onCreated,
}: {
  opportunityId: number;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState<SupplierLeadInput>({
    supplier_name: "",
    country: "",
    email: "",
    price_mt: null,
    quoted_incoterms: "FOB",
    credibility_score: 50,
    responsiveness_score: 50,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.createSupplierLead(opportunityId, form);
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
        <h2 className="text-lg font-semibold">Add supplier lead</h2>
        <label className="block text-sm">
          <span className="text-gray-400">Supplier name</span>
          <input
            className="input mt-1 w-full"
            required
            value={form.supplier_name ?? ""}
            onChange={(e) =>
              setForm({ ...form, supplier_name: e.target.value })
            }
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Country</span>
            <input
              className="input mt-1 w-full"
              value={form.country ?? ""}
              onChange={(e) => setForm({ ...form, country: e.target.value })}
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Email</span>
            <input
              className="input mt-1 w-full"
              type="email"
              value={form.email ?? ""}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Quoted price ($/MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              value={form.price_mt ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  price_mt: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Quoted incoterms</span>
            <select
              className="input mt-1 w-full"
              value={form.quoted_incoterms ?? "FOB"}
              onChange={(e) =>
                setForm({ ...form, quoted_incoterms: e.target.value })
              }
            >
              <option value="FOB">FOB</option>
              <option value="CFR">CFR</option>
              <option value="CIF">CIF</option>
              <option value="DAP">DAP</option>
            </select>
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Credibility (0-100)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              min={0}
              max={100}
              value={form.credibility_score ?? 50}
              onChange={(e) =>
                setForm({ ...form, credibility_score: Number(e.target.value) })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Responsiveness (0-100)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              min={0}
              max={100}
              value={form.responsiveness_score ?? 50}
              onChange={(e) =>
                setForm({
                  ...form,
                  responsiveness_score: Number(e.target.value),
                })
              }
            />
          </label>
        </div>
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
            {submitting ? "Adding…" : "Add supplier"}
          </button>
        </div>
      </form>
    </div>
  );
}

function NewBuyerLeadModal({
  opportunityId,
  defaultTargetPrice,
  defaultVolume,
  onClose,
  onCreated,
}: {
  opportunityId: number;
  defaultTargetPrice: number | null;
  defaultVolume: number;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState<BuyerLeadInput>({
    buyer_name: "",
    country: "",
    email: "",
    target_price_mt: defaultTargetPrice,
    volume_mt: defaultVolume,
    appetite: "medium",
    urgency: "medium",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.createBuyerLead(opportunityId, form);
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
        <h2 className="text-lg font-semibold">Add buyer lead</h2>
        <label className="block text-sm">
          <span className="text-gray-400">Buyer name</span>
          <input
            className="input mt-1 w-full"
            required
            value={form.buyer_name ?? ""}
            onChange={(e) => setForm({ ...form, buyer_name: e.target.value })}
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Country</span>
            <input
              className="input mt-1 w-full"
              value={form.country ?? ""}
              onChange={(e) => setForm({ ...form, country: e.target.value })}
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Email</span>
            <input
              className="input mt-1 w-full"
              type="email"
              value={form.email ?? ""}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Target price ($/MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              value={form.target_price_mt ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  target_price_mt: e.target.value
                    ? Number(e.target.value)
                    : null,
                })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Volume (MT)</span>
            <input
              className="input mt-1 w-full"
              type="number"
              value={form.volume_mt ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  volume_mt: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-gray-400">Appetite</span>
            <select
              className="input mt-1 w-full"
              value={form.appetite ?? "medium"}
              onChange={(e) =>
                setForm({
                  ...form,
                  appetite: e.target.value as "low" | "medium" | "high",
                })
              }
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-gray-400">Urgency</span>
            <select
              className="input mt-1 w-full"
              value={form.urgency ?? "medium"}
              onChange={(e) =>
                setForm({
                  ...form,
                  urgency: e.target.value as "low" | "medium" | "high",
                })
              }
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </label>
        </div>
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
            {submitting ? "Adding…" : "Add buyer"}
          </button>
        </div>
      </form>
    </div>
  );
}
