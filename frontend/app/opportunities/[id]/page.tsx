"use client";

import { useParams, useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { AppShell } from "../../../components/AppShell";
import Link from "next/link";
import {
  api,
  BuyerLead,
  BuyerLeadInput,
  CuratedCounterparty,
  HealthScore,
  MatchPair,
  MatchingResult,
  NEGOTIATION_STAGE_LABELS,
  NextAction,
  NextActionsOut,
  Opportunity,
  OPPORTUNITY_STATUS_LABELS,
  OpportunityDashboard,
  Supplier,
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
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

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

  async function onSyncReplies() {
    setSyncing(true);
    setError(null);
    setSyncMsg(null);
    try {
      const res = await api.syncReplies();
      const forThis = res.new_messages.filter(
        (m) => m.opportunity_id === id,
      ).length;
      if (res.mode === "offline") {
        setSyncMsg(
          "Gmail is offline (no credentials) — nothing to sync. Add a Gmail App Password to pull live replies.",
        );
      } else if (res.new_messages.length === 0) {
        setSyncMsg(`Checked inbox (${res.fetched} scanned) — no new replies.`);
      } else {
        setSyncMsg(
          `Synced ${res.new_messages.length} new repl${
            res.new_messages.length === 1 ? "y" : "ies"
          } · ${res.matched} matched to leads (${forThis} on this opportunity).`,
        );
      }
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  return (
    <AppShell>
      <OpportunityHeader
        opp={opp}
        health={health}
        onStatusChange={onStatusChange}
        onSyncReplies={onSyncReplies}
        syncing={syncing}
      />

      {syncMsg && (
        <div className="mt-3 rounded-md border border-accent/30 bg-accent/10 p-3 text-sm text-gray-200">
          {syncMsg}
        </div>
      )}

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
          opportunityCommodity={opp.commodity}
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
          opportunityCommodity={opp.commodity}
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
  onSyncReplies,
  syncing,
}: {
  opp: Opportunity;
  health: HealthScore;
  onStatusChange: (s: string) => void;
  onSyncReplies: () => void;
  syncing: boolean;
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
        <button
          className="btn-ghost text-sm"
          onClick={onSyncReplies}
          disabled={syncing}
          title="Pull Gmail replies and advance matched leads"
        >
          {syncing ? "Syncing…" : "Sync replies"}
        </button>
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
  opportunityCommodity,
  onChange,
  onAdd,
}: {
  opportunityId: number;
  supplierLeads: SupplierLead[];
  opportunityCommodity?: string | null;
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
      <CuratedSuppliersPanel
        opportunityId={opportunityId}
        opportunityCommodity={opportunityCommodity}
        supplierLeads={supplierLeads}
        onChange={onChange}
      />
      <table className="w-full text-xs">
        <thead className="text-left text-gray-500">
          <tr>
            <th className="pb-1.5 font-medium">Name / country</th>
            <th className="font-medium">Contact</th>
            <th className="font-medium">Stage</th>
            <th className="font-medium">Price</th>
            <th className="font-medium">Credibility</th>
            <th className="font-medium">Response</th>
            <th className="font-medium">Status</th>
            <th className="font-medium">Last contact</th>
            <th className="font-medium" />
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
              <td colSpan={8} className="py-6 text-center text-gray-500">
                No supplier leads yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function CuratedSuppliersPanel({
  opportunityId,
  opportunityCommodity,
  supplierLeads,
  onChange,
}: {
  opportunityId: number;
  opportunityCommodity?: string | null;
  supplierLeads: SupplierLead[];
  onChange: () => void;
}) {
  const [entries, setEntries] = useState<CuratedCounterparty[] | null>(null);
  const [open, setOpen] = useState(false);
  const [seedingAll, setSeedingAll] = useState(false);
  const [seedingName, setSeedingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!opportunityCommodity) {
      setEntries([]);
      return;
    }
    (async () => {
      try {
        const list = await api.listCuratedSuppliers(opportunityId);
        if (!cancelled) setEntries(list);
      } catch (e) {
        if (!cancelled)
          setError(
            e instanceof Error ? e.message : "Failed to load curated list",
          );
      }
    })();
    return () => {
      cancelled = true;
    };
    // Re-fetch whenever the supplier-lead set changes so the "already added"
    // flag stays in sync after the user adds a curated entry from elsewhere.
  }, [opportunityId, opportunityCommodity, supplierLeads.length]);

  if (!entries || entries.length === 0) return null;

  const remaining = entries.filter((e) => !e.already_added);

  async function seedAll() {
    setSeedingAll(true);
    setError(null);
    try {
      await api.seedCuratedSuppliers(opportunityId, []);
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to seed");
    } finally {
      setSeedingAll(false);
    }
  }

  async function seedOne(name: string) {
    setSeedingName(name);
    setError(null);
    try {
      await api.seedCuratedSuppliers(opportunityId, [name]);
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to seed");
    } finally {
      setSeedingName(null);
    }
  }

  return (
    <div className="mb-3 rounded-md border border-emerald-500/20 bg-emerald-500/5 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs">
          <div className="font-medium text-emerald-300">
            Curated counterparties · vetted for {entries[0].commodity}
          </div>
          <div className="mt-0.5 text-gray-400">
            {entries.length} pre-vetted desk{entries.length === 1 ? "" : "s"} for
            this lane.
            {remaining.length === 0
              ? " All already added."
              : ` ${remaining.length} not yet attached.`}
          </div>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "hide" : "show"}
          </button>
          {remaining.length > 0 && (
            <button
              type="button"
              className="rounded-md bg-emerald-600 px-2 py-1 text-xs font-medium text-black hover:bg-emerald-500 disabled:opacity-50"
              onClick={seedAll}
              disabled={seedingAll}
            >
              {seedingAll
                ? "Adding…"
                : `+ Add all curated (${remaining.length})`}
            </button>
          )}
        </div>
      </div>
      {error && (
        <div className="mt-2 text-xs text-red-300">{error}</div>
      )}
      {open && (
        <ul className="mt-3 space-y-2">
          {entries.map((cp) => (
            <li
              key={cp.name}
              className="flex items-start justify-between gap-3 rounded-md bg-black/20 p-2"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-xs">
                  <span className="font-medium text-gray-100">{cp.name}</span>
                  <span className="text-[10px] text-gray-500">
                    {cp.country} · {cp.type}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-gray-400">
                  {cp.description}
                </div>
                <a
                  href={cp.website}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-0.5 inline-block text-[11px] text-accent hover:underline"
                >
                  {cp.website.replace(/^https?:\/\//, "")}
                </a>
              </div>
              <div className="shrink-0">
                {cp.already_added ? (
                  <span className="text-[11px] text-gray-500">added</span>
                ) : (
                  <button
                    type="button"
                    className="btn-ghost text-[11px]"
                    onClick={() => seedOne(cp.name)}
                    disabled={seedingName === cp.name}
                  >
                    {seedingName === cp.name ? "adding…" : "add"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
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
  const [expanded, setExpanded] = useState(false);
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
    <>
      <tr className="row">
        <td className="py-1.5">
          <div className="text-gray-200">{lead.supplier_name || "—"}</div>
          <div className="text-[11px] text-gray-500">{lead.country || "—"}</div>
        </td>
        <td>
          {lead.email ? (
            <div>
              <a
                href={`mailto:${lead.email}`}
                className="text-accent hover:underline"
                title={lead.email}
              >
                {lead.contact_name || lead.email}
              </a>
              {lead.contact_title && (
                <div className="text-[10px] text-gray-500">{lead.contact_title}</div>
              )}
            </div>
          ) : (
            <span className="text-gray-600 italic">no contact</span>
          )}
        </td>
        <td>
          <StageBadge stage={lead.negotiation_stage} />
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
            <button
              className="text-accent hover:underline"
              onClick={markContacted}
            >
              log contact
            </button>
            <button className="text-red-400 hover:underline" onClick={remove}>
              remove
            </button>
          </div>
        </td>
        <td className="text-right">
          <button
            className="btn-ghost text-[11px]"
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? "Collapse" : "Edit details + audit"}
          >
            {expanded ? "close" : "edit"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} className="bg-black/20 px-3 py-3">
            <SupplierLeadEditor
              opportunityId={opportunityId}
              lead={lead}
              onChange={onChange}
              onClose={() => setExpanded(false)}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function StageBadge({ stage }: { stage: number }) {
  const s = Math.min(5, Math.max(1, stage || 1));
  const label = NEGOTIATION_STAGE_LABELS[s] || `Stage ${s}`;
  const colour =
    s === 1
      ? "bg-gray-700 text-gray-200"
      : s === 2
        ? "bg-blue-900/60 text-blue-200"
        : s === 3
          ? "bg-amber-900/60 text-amber-200"
          : s === 4
            ? "bg-purple-900/60 text-purple-200"
            : "bg-emerald-900/60 text-emerald-200";
  return (
    <span
      className={classNames(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium",
        colour,
      )}
      title={`Negotiation stage ${s} of 5`}
    >
      {s}/5 · {label}
    </span>
  );
}

function AuditPanel({
  intel,
  disclosed,
}: {
  intel: Record<string, unknown>;
  disclosed: Record<string, unknown>;
}) {
  const intelKeys = Object.keys(intel || {});
  const disclosedKeys = Object.keys(disclosed || {});
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <div className="rounded border border-emerald-900/60 bg-emerald-950/20 p-2.5">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-300">
          What we know about them
        </div>
        {intelKeys.length === 0 ? (
          <div className="text-[11px] italic text-gray-500">
            No intel yet. It fills in as replies are logged.
          </div>
        ) : (
          <ul className="space-y-0.5 text-[11px] text-gray-200">
            {intelKeys.map((k) => (
              <li key={k}>
                <span className="text-gray-400">{k}:</span>{" "}
                <span className="font-mono">
                  {JSON.stringify((intel as Record<string, unknown>)[k])}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="rounded border border-amber-900/60 bg-amber-950/20 p-2.5">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-300">
          What we&apos;ve told them
        </div>
        {disclosedKeys.length === 0 ? (
          <div className="text-[11px] italic text-gray-500">
            Nothing disclosed yet.
          </div>
        ) : (
          <ul className="space-y-0.5 text-[11px] text-gray-200">
            {disclosedKeys.map((k) => (
              <li key={k}>
                <span className="text-gray-400">{k}:</span>{" "}
                <span className="font-mono">
                  {JSON.stringify(
                    (disclosed as Record<string, unknown>)[k],
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function SupplierLeadEditor({
  opportunityId,
  lead,
  onChange,
  onClose,
}: {
  opportunityId: number;
  lead: SupplierLead;
  onChange: () => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const [form, setForm] = useState<{
    price_mt: number | "";
    quoted_incoterms: string;
    min_order_mt: number | "";
    lead_time_days: number | "";
    payment_terms: string;
    credibility_score: number;
    responsiveness_score: number;
    negotiation_stage: number;
    notes: string;
  }>({
    price_mt: lead.price_mt ?? "",
    quoted_incoterms: lead.quoted_incoterms ?? "",
    min_order_mt: lead.min_order_mt ?? "",
    lead_time_days: lead.lead_time_days ?? "",
    payment_terms: lead.payment_terms ?? "",
    credibility_score: lead.credibility_score,
    responsiveness_score: lead.responsiveness_score,
    negotiation_stage: lead.negotiation_stage,
    notes: lead.notes ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [composing, setComposing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      const payload: SupplierLeadInput = {
        price_mt: form.price_mt === "" ? null : Number(form.price_mt),
        quoted_incoterms: form.quoted_incoterms || null,
        min_order_mt: form.min_order_mt === "" ? null : Number(form.min_order_mt),
        lead_time_days:
          form.lead_time_days === "" ? null : Number(form.lead_time_days),
        payment_terms: form.payment_terms || null,
        credibility_score: form.credibility_score,
        responsiveness_score: form.responsiveness_score,
        negotiation_stage: form.negotiation_stage,
        notes: form.notes || null,
      };
      await api.updateSupplierLead(opportunityId, lead.id, payload);
      onChange();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function composeStageEmail() {
    setComposing(true);
    setErr(null);
    try {
      const doc = await api.generateDocument({
        type:
          lead.negotiation_stage === 1 ? "outreach_email" : "follow_up_email",
        opportunity_id: opportunityId,
        supplier_lead_id: lead.id,
      });
      router.push(`/documents/${doc.id}`);
    } catch (e) {
      setErr((e as Error).message);
      setComposing(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <LabeledInput
          label="Price $/MT"
          type="number"
          value={form.price_mt}
          onChange={(v) =>
            setForm({ ...form, price_mt: v === "" ? "" : Number(v) })
          }
        />
        <LabeledInput
          label="Incoterms"
          value={form.quoted_incoterms}
          onChange={(v) => setForm({ ...form, quoted_incoterms: String(v) })}
          placeholder="FOB, CFR, …"
        />
        <LabeledInput
          label="MOQ (MT)"
          type="number"
          value={form.min_order_mt}
          onChange={(v) =>
            setForm({ ...form, min_order_mt: v === "" ? "" : Number(v) })
          }
        />
        <LabeledInput
          label="Lead time (days)"
          type="number"
          value={form.lead_time_days}
          onChange={(v) =>
            setForm({ ...form, lead_time_days: v === "" ? "" : Number(v) })
          }
        />
        <LabeledInput
          label="Payment terms"
          value={form.payment_terms}
          onChange={(v) => setForm({ ...form, payment_terms: String(v) })}
          placeholder="DLC 30d, SBLC, …"
        />
        <LabeledInput
          label="Credibility (0-100)"
          type="number"
          value={form.credibility_score}
          onChange={(v) =>
            setForm({ ...form, credibility_score: Number(v) || 0 })
          }
        />
        <LabeledInput
          label="Responsiveness (0-100)"
          type="number"
          value={form.responsiveness_score}
          onChange={(v) =>
            setForm({ ...form, responsiveness_score: Number(v) || 0 })
          }
        />
        <div className="flex flex-col">
          <label className="mb-0.5 text-[10px] uppercase tracking-wide text-gray-500">
            Negotiation stage
          </label>
          <select
            className="input text-xs"
            value={form.negotiation_stage}
            onChange={(e) =>
              setForm({ ...form, negotiation_stage: Number(e.target.value) })
            }
          >
            {[1, 2, 3, 4, 5].map((s) => (
              <option key={s} value={s}>
                {s}/5 · {NEGOTIATION_STAGE_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="mb-0.5 block text-[10px] uppercase tracking-wide text-gray-500">
          Notes
        </label>
        <textarea
          className="input w-full text-xs"
          rows={2}
          value={form.notes}
          onChange={(e) => setForm({ ...form, notes: e.target.value })}
          placeholder="What they quoted, what they asked, what to push on next…"
        />
      </div>

      <AuditPanel intel={lead.intel} disclosed={lead.disclosed} />

      {err && <div className="text-xs text-red-400">{err}</div>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          className="btn-primary text-xs"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
        <button
          className="btn-ghost text-xs"
          onClick={composeStageEmail}
          disabled={composing}
          title="Generate a stage-aware email (price rules baked in)"
        >
          {composing
            ? "Drafting…"
            : `Compose stage-${lead.negotiation_stage} email`}
        </button>
        <button className="btn-ghost text-xs" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  value: string | number | "" | null;
  onChange: (v: string | number | "") => void;
  type?: "text" | "number";
  placeholder?: string;
}) {
  return (
    <div className="flex flex-col">
      <label className="mb-0.5 text-[10px] uppercase tracking-wide text-gray-500">
        {label}
      </label>
      <input
        className="input text-xs"
        type={type}
        value={value == null ? "" : value}
        placeholder={placeholder}
        onChange={(e) => {
          const v = e.target.value;
          if (type === "number") {
            onChange(v === "" ? "" : Number(v));
          } else {
            onChange(v);
          }
        }}
      />
    </div>
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
            <th className="font-medium">Stage</th>
            <th className="font-medium">Target</th>
            <th className="font-medium">Appetite</th>
            <th className="font-medium">Urgency</th>
            <th className="font-medium">Status</th>
            <th className="font-medium">Last contact</th>
            <th className="font-medium" />
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
              <td colSpan={8} className="py-6 text-center text-gray-500">
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
  const [expanded, setExpanded] = useState(false);
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
    <>
      <tr className="row">
        <td className="py-1.5">
          <div className="text-gray-200">{lead.buyer_name || "—"}</div>
          <div className="text-[11px] text-gray-500">{lead.country || "—"}</div>
        </td>
        <td>
          <StageBadge stage={lead.negotiation_stage} />
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
            <button
              className="text-accent hover:underline"
              onClick={markContacted}
            >
              log contact
            </button>
            <button className="text-red-400 hover:underline" onClick={remove}>
              remove
            </button>
          </div>
        </td>
        <td className="text-right">
          <button
            className="btn-ghost text-[11px]"
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? "Collapse" : "Edit details + audit"}
          >
            {expanded ? "close" : "edit"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} className="bg-black/20 px-3 py-3">
            <BuyerLeadEditor
              opportunityId={opportunityId}
              lead={lead}
              onChange={onChange}
              onClose={() => setExpanded(false)}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function BuyerLeadEditor({
  opportunityId,
  lead,
  onChange,
  onClose,
}: {
  opportunityId: number;
  lead: BuyerLead;
  onChange: () => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const [form, setForm] = useState<{
    target_price_mt: number | "";
    volume_mt: number | "";
    appetite: "low" | "medium" | "high";
    urgency: "low" | "medium" | "high";
    negotiation_stage: number;
    feedback: string;
    notes: string;
  }>({
    target_price_mt: lead.target_price_mt ?? "",
    volume_mt: lead.volume_mt ?? "",
    appetite: lead.appetite,
    urgency: lead.urgency,
    negotiation_stage: lead.negotiation_stage,
    feedback: lead.feedback ?? "",
    notes: lead.notes ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [composing, setComposing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      const payload: BuyerLeadInput = {
        target_price_mt:
          form.target_price_mt === "" ? null : Number(form.target_price_mt),
        volume_mt: form.volume_mt === "" ? null : Number(form.volume_mt),
        appetite: form.appetite,
        urgency: form.urgency,
        negotiation_stage: form.negotiation_stage,
        feedback: form.feedback || null,
        notes: form.notes || null,
      };
      await api.updateBuyerLead(opportunityId, lead.id, payload);
      onChange();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function composeStageEmail() {
    setComposing(true);
    setErr(null);
    try {
      const doc = await api.generateDocument({
        type:
          lead.negotiation_stage === 1 ? "outreach_email" : "follow_up_email",
        opportunity_id: opportunityId,
        buyer_lead_id: lead.id,
      });
      router.push(`/documents/${doc.id}`);
    } catch (e) {
      setErr((e as Error).message);
      setComposing(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <LabeledInput
          label="Target $/MT"
          type="number"
          value={form.target_price_mt}
          onChange={(v) =>
            setForm({
              ...form,
              target_price_mt: v === "" ? "" : Number(v),
            })
          }
        />
        <LabeledInput
          label="Volume (MT)"
          type="number"
          value={form.volume_mt}
          onChange={(v) =>
            setForm({ ...form, volume_mt: v === "" ? "" : Number(v) })
          }
        />
        <div className="flex flex-col">
          <label className="mb-0.5 text-[10px] uppercase tracking-wide text-gray-500">
            Appetite
          </label>
          <select
            className="input text-xs"
            value={form.appetite}
            onChange={(e) =>
              setForm({
                ...form,
                appetite: e.target.value as "low" | "medium" | "high",
              })
            }
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </div>
        <div className="flex flex-col">
          <label className="mb-0.5 text-[10px] uppercase tracking-wide text-gray-500">
            Urgency
          </label>
          <select
            className="input text-xs"
            value={form.urgency}
            onChange={(e) =>
              setForm({
                ...form,
                urgency: e.target.value as "low" | "medium" | "high",
              })
            }
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </div>
        <div className="flex flex-col md:col-span-2">
          <label className="mb-0.5 text-[10px] uppercase tracking-wide text-gray-500">
            Negotiation stage
          </label>
          <select
            className="input text-xs"
            value={form.negotiation_stage}
            onChange={(e) =>
              setForm({ ...form, negotiation_stage: Number(e.target.value) })
            }
          >
            {[1, 2, 3, 4, 5].map((s) => (
              <option key={s} value={s}>
                {s}/5 · {NEGOTIATION_STAGE_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="mb-0.5 block text-[10px] uppercase tracking-wide text-gray-500">
          Feedback
        </label>
        <textarea
          className="input w-full text-xs"
          rows={2}
          value={form.feedback}
          onChange={(e) => setForm({ ...form, feedback: e.target.value })}
          placeholder="What the buyer said on the last call or email…"
        />
      </div>
      <div>
        <label className="mb-0.5 block text-[10px] uppercase tracking-wide text-gray-500">
          Notes
        </label>
        <textarea
          className="input w-full text-xs"
          rows={2}
          value={form.notes}
          onChange={(e) => setForm({ ...form, notes: e.target.value })}
        />
      </div>

      <AuditPanel intel={lead.intel} disclosed={lead.disclosed} />

      {err && <div className="text-xs text-red-400">{err}</div>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          className="btn-primary text-xs"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
        <button
          className="btn-ghost text-xs"
          onClick={composeStageEmail}
          disabled={composing}
          title="Generate a stage-aware email (price rules baked in)"
        >
          {composing
            ? "Drafting…"
            : `Compose stage-${lead.negotiation_stage} email`}
        </button>
        <button className="btn-ghost text-xs" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
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
  opportunityCommodity,
  onClose,
  onCreated,
}: {
  opportunityId: number;
  opportunityCommodity?: string | null;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [mode, setMode] = useState<"pick" | "manual">("pick");
  const [search, setSearch] = useState("");
  const [library, setLibrary] = useState<Supplier[] | null>(null);
  const [pickedId, setPickedId] = useState<number | null>(null);
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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api.listSuppliers({
          q: search || undefined,
          commodity: opportunityCommodity || undefined,
        });
        if (!cancelled) setLibrary(list);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Failed to load suppliers");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [search, opportunityCommodity]);

  const picked = library?.find((s) => s.id === pickedId) ?? null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "pick") {
        if (!picked) {
          setError("Pick a supplier from the list or switch to manual entry.");
          setSubmitting(false);
          return;
        }
        await api.createSupplierLead(opportunityId, {
          supplier_id: picked.id,
          supplier_name: picked.name,
          country: picked.country ?? undefined,
          email: picked.email ?? undefined,
          price_mt: form.price_mt ?? null,
          quoted_incoterms: form.quoted_incoterms ?? "FOB",
          credibility_score:
            typeof picked.credibility_score === "number"
              ? picked.credibility_score
              : form.credibility_score ?? 50,
          responsiveness_score: form.responsiveness_score ?? 50,
        });
      } else {
        await api.createSupplierLead(opportunityId, form);
      }
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
        className="card w-full max-w-2xl space-y-3 p-5"
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">Add supplier lead</h2>
            <p className="mt-1 text-xs text-gray-400">
              Pick from your supplier library, or add a new one manually.
              Run <Link className="text-accent underline" href="/suppliers">AI Discover</Link>{" "}
              first to populate your library for this commodity.
            </p>
          </div>
          <div className="flex rounded-md border border-gray-700 text-xs">
            <button
              type="button"
              onClick={() => setMode("pick")}
              className={classNames(
                "px-3 py-1",
                mode === "pick" ? "bg-accent text-black" : "text-gray-300"
              )}
            >
              From library
            </button>
            <button
              type="button"
              onClick={() => setMode("manual")}
              className={classNames(
                "px-3 py-1",
                mode === "manual" ? "bg-accent text-black" : "text-gray-300"
              )}
            >
              Manual entry
            </button>
          </div>
        </div>

        {mode === "pick" ? (
          <div className="space-y-2">
            <input
              className="input w-full"
              placeholder="Search suppliers by name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="max-h-64 overflow-auto rounded-md border border-gray-700">
              {library === null && (
                <div className="p-3 text-xs text-gray-500">Loading…</div>
              )}
              {library && library.length === 0 && (
                <div className="p-3 text-xs text-gray-500">
                  No suppliers in your library yet. Switch to{" "}
                  <button
                    type="button"
                    className="underline"
                    onClick={() => setMode("manual")}
                  >
                    manual entry
                  </button>
                  , or{" "}
                  <Link className="underline" href="/suppliers">
                    run AI Discover
                  </Link>{" "}
                  to find suppliers for{" "}
                  {opportunityCommodity || "this commodity"}.
                </div>
              )}
              {library && library.length > 0 && (
                <ul className="divide-y divide-gray-800">
                  {library.map((s) => (
                    <li key={s.id}>
                      <button
                        type="button"
                        onClick={() => setPickedId(s.id)}
                        className={classNames(
                          "flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-surface2",
                          pickedId === s.id && "bg-surface2"
                        )}
                      >
                        <div className="min-w-0">
                          <div className="truncate font-medium text-gray-100">
                            {s.name}
                          </div>
                          <div className="truncate text-xs text-gray-500">
                            {(s.type ?? "unknown")} · {s.country || "—"} ·{" "}
                            {s.email || "no email"}
                          </div>
                        </div>
                        <div className="whitespace-nowrap text-xs text-gray-400">
                          cred {s.credibility_score}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {picked && (
              <div className="rounded-md bg-surface2 p-2 text-xs text-gray-300">
                Selected: <span className="font-medium">{picked.name}</span>
                {picked.email ? ` · ${picked.email}` : " · no email on file"}
              </div>
            )}
          </div>
        ) : (
        <>
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
        </>
        )}

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
