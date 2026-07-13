"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../../components/AppShell";
import { api, Document, GmailStatus } from "../../../lib/api";

type NegotiationBlock = {
  stage?: number;
  stage_label?: string;
  side?: string;
  reveal?: string[];
  ask?: string[];
  hold?: string[];
  tactics?: string[];
  known_intel?: Record<string, unknown>;
  previously_disclosed?: Record<string, unknown>;
  market_reference?: MarketRef | null;
  supplier_quote?: SupplierQuote | null;
};

type MarketRef = {
  exchange?: string;
  ticker?: string;
  price_mt?: number;
  timestamp?: number;
};

type SupplierQuote = {
  price_mt?: number;
  incoterms?: string | null;
  payment_terms?: string | null;
};

export default function DocumentPage() {
  const params = useParams();
  const router = useRouter();
  const id = Number(params?.id);

  const [doc, setDoc] = useState<Document | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const d = await api.getDocument(id);
      setDoc(d);
      setTitle(d.title);
      setContent(d.content);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load document");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const negotiation = useMemo<NegotiationBlock | null>(() => {
    const raw = doc?.inputs?.negotiation;
    if (raw && typeof raw === "object") return raw as NegotiationBlock;
    return null;
  }, [doc]);

  const supplier = useMemo<{ name?: string; country?: string } | null>(() => {
    const raw = doc?.inputs?.supplier;
    if (raw && typeof raw === "object")
      return raw as { name?: string; country?: string };
    return null;
  }, [doc]);

  async function save() {
    if (!doc) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateDocument(doc.id, { title, content });
      setDoc(updated);
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function copyToClipboard() {
    try {
      await navigator.clipboard.writeText(content);
      setSavedAt(Date.now());
    } catch {
      /* clipboard API may be unavailable; ignore */
    }
  }

  const dirty = doc ? title !== doc.title || content !== doc.content : false;

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <button
            onClick={() => router.back()}
            className="text-sm text-gray-400 hover:text-gray-200"
          >
            ← Back
          </button>
          <h1 className="mt-1 text-2xl font-semibold">
            {doc ? doc.title : loading ? "Loading…" : "Document"}
          </h1>
          {doc && (
            <p className="text-xs uppercase tracking-wide text-gray-500">
              {doc.type.replace(/_/g, " ")}
              {supplier?.name ? ` · ${supplier.name}` : ""}
              {supplier?.country ? ` · ${supplier.country}` : ""}
            </p>
          )}
        </div>
        {doc && (
          <div className="flex items-center gap-2">
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
            <button onClick={copyToClipboard} className="btn-ghost">
              Copy
            </button>
            <button
              onClick={save}
              className="btn-primary"
              disabled={!dirty || saving}
            >
              {saving ? "Saving…" : dirty ? "Save" : "Saved"}
            </button>
          </div>
        )}
      </div>

      {error && (
        <div className="card mb-4 border border-red-900/50 bg-red-950/30 text-sm text-red-200">
          {error}
        </div>
      )}

      {savedAt && !dirty && !error && (
        <div className="mb-4 text-xs text-green-400">
          Saved at {new Date(savedAt).toLocaleTimeString()}
        </div>
      )}

      {doc && (
        <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
          <div className="card">
            <div className="mb-2">
              <label className="text-xs uppercase tracking-wide text-gray-500">
                Title
              </label>
              <input
                className="input mt-1"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <label className="text-xs uppercase tracking-wide text-gray-500">
              Draft
            </label>
            <textarea
              className="textarea mt-1 min-h-[60vh] whitespace-pre-wrap font-mono text-sm"
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
            <p className="mt-2 text-xs text-gray-500">
              This is a draft. Review before sending — nothing is sent
              automatically.
            </p>
          </div>

          <div className="space-y-4">
            <SendPanel doc={doc} onSent={load} />
            {negotiation && <NegotiationPanel n={negotiation} />}
          </div>
        </div>
      )}
    </AppShell>
  );
}

function readNum(inputs: Record<string, unknown>, key: string): number | undefined {
  const v = inputs?.[key];
  return typeof v === "number" ? v : undefined;
}

function SendPanel({ doc, onSent }: { doc: Document; onSent: () => void }) {
  const inputs = doc.inputs || {};
  const supplierBlock =
    inputs.supplier && typeof inputs.supplier === "object"
      ? (inputs.supplier as { email?: string })
      : null;
  const buyerBlock =
    inputs.buyer && typeof inputs.buyer === "object"
      ? (inputs.buyer as { email?: string })
      : null;

  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [to, setTo] = useState(
    supplierBlock?.email || buyerBlock?.email || "",
  );
  const [subject, setSubject] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .gmailStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  async function send() {
    if (!to.trim()) {
      setError("Enter a recipient email.");
      return;
    }
    setSending(true);
    setError(null);
    setResult(null);
    try {
      const msg = await api.sendDocument({
        document_id: doc.id,
        to_email: to.trim(),
        subject: subject.trim() || undefined,
        opportunity_id: readNum(inputs, "opportunity_id"),
        supplier_lead_id: readNum(inputs, "supplier_lead_id"),
        buyer_lead_id: readNum(inputs, "buyer_lead_id"),
      });
      setResult(
        msg.status === "offline"
          ? `Recorded offline (no Gmail credentials). Would have gone to ${msg.to_email}.`
          : `Sent to ${msg.to_email}.`,
      );
      onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed");
    } finally {
      setSending(false);
    }
  }

  const offline = !status?.configured;

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wide text-gray-500">
          Send via Gmail
        </div>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
            offline
              ? "bg-gray-700/50 text-gray-300"
              : "bg-green-900/40 text-green-200"
          }`}
        >
          {offline ? "Offline" : "Live"}
        </span>
      </div>

      {status?.address && (
        <div className="text-xs text-gray-500">From: {status.address}</div>
      )}

      <label className="block text-xs">
        <span className="text-gray-500">To</span>
        <input
          className="input mt-1 w-full text-sm"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          placeholder="counterparty@example.com"
        />
      </label>
      <label className="block text-xs">
        <span className="text-gray-500">Subject (optional — taken from draft)</span>
        <input
          className="input mt-1 w-full text-sm"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Leave blank to use the draft's Subject line"
        />
      </label>

      <button className="btn-primary w-full" onClick={send} disabled={sending}>
        {sending ? "Sending…" : offline ? "Record send (offline)" : "Send email"}
      </button>

      {result && <div className="text-xs text-green-400">{result}</div>}
      {error && <div className="text-xs text-red-400">{error}</div>}

      <p className="text-[11px] leading-snug text-gray-500">
        Review the draft on the left before sending — Atlas only sends what you
        approve here. Replies sync back to the lead from the Strategy board.
      </p>
    </div>
  );
}

function NegotiationPanel({ n }: { n: NegotiationBlock }) {
  const stage = n.stage ?? 1;
  const label = n.stage_label ?? "";
  const mkt = n.market_reference ?? null;
  const quote = n.supplier_quote ?? null;
  return (
    <div className="card space-y-3">
      <div>
        <div className="text-xs uppercase tracking-wide text-gray-500">
          Negotiation stage
        </div>
        <div className="mt-1 inline-flex rounded bg-amber-900/40 px-2 py-1 text-sm text-amber-200">
          {stage}/5 {label ? `· ${label}` : ""}
        </div>
      </div>

      {quote?.price_mt != null && (
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-500">
            Supplier quote
          </div>
          <div className="mt-1 text-sm">
            ${Number(quote.price_mt).toFixed(2)}/MT
            {quote.incoterms ? ` · ${quote.incoterms}` : ""}
            {quote.payment_terms ? ` · ${quote.payment_terms}` : ""}
          </div>
        </div>
      )}

      {mkt?.price_mt != null && (
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-500">
            Market reference
          </div>
          <div className="mt-1 text-sm">
            {mkt.exchange ?? ""} {mkt.ticker ?? ""} · $
            {Number(mkt.price_mt).toFixed(2)}/MT
          </div>
        </div>
      )}

      {n.reveal && n.reveal.length > 0 && (
        <DisclosureList title="Reveal at this stage" items={n.reveal} tone="green" />
      )}
      {n.ask && n.ask.length > 0 && (
        <DisclosureList title="Ask them" items={n.ask} tone="blue" />
      )}
      {n.hold && n.hold.length > 0 && (
        <DisclosureList title="Hold back" items={n.hold} tone="red" />
      )}
      {n.tactics && n.tactics.length > 0 && (
        <DisclosureList title="Tactics" items={n.tactics} tone="gray" />
      )}
    </div>
  );
}

function DisclosureList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "green" | "blue" | "red" | "gray";
}) {
  const toneClass = {
    green: "text-green-300",
    blue: "text-blue-300",
    red: "text-red-300",
    gray: "text-gray-300",
  }[tone];
  return (
    <div>
      <div className={`text-xs uppercase tracking-wide ${toneClass}`}>
        {title}
      </div>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-gray-300">
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

