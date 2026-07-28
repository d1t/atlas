"use client";

import { useCallback, useEffect, useState } from "react";
import { api, CommodityQuote } from "../lib/api";

type Props = {
  commodity: string;
  /** Optional buy/sell price (same currency) for a comparison badge. */
  buyPrice?: number;
  sellPrice?: number;
  /** Compact layout for embedding in a larger card. */
  compact?: boolean;
};

function formatTimestamp(unixSec: number): string {
  try {
    return new Date(unixSec * 1000).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return "—";
  }
}

function formatMoney(n: number | null | undefined, currency = "USD"): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(n);
}

const SOURCE_LABELS: Record<string, string> = {
  cnbc: "CNBC",
  yahoo_finance: "Yahoo Finance",
};

function deviationClass(pct: number): string {
  const a = Math.abs(pct);
  if (a <= 10) return "text-success";
  if (a <= 25) return "text-yellow-400";
  return "text-danger";
}

export function PriceDisplay({ commodity, buyPrice, sellPrice, compact }: Props) {
  const [quote, setQuote] = useState<CommodityQuote | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState(true);

  const fetchQuote = useCallback(
    async (refresh = false) => {
      if (!commodity) return;
      setLoading(true);
      setError(null);
      try {
        const q = await api.getPrice(commodity, refresh);
        setQuote(q);
        setSupported(true);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Failed to fetch price";
        if (msg.includes("404")) {
          setSupported(false);
          setQuote(null);
        } else {
          setError(msg);
        }
      } finally {
        setLoading(false);
      }
    },
    [commodity],
  );

  useEffect(() => {
    fetchQuote(false);
    // Auto-refresh every 5 min to match backend cache TTL
    const id = setInterval(() => fetchQuote(false), 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [fetchQuote]);

  if (!supported) {
    return (
      <div className="rounded-md bg-surface2 px-3 py-2 text-xs text-gray-500">
        No futures reference configured for <span className="font-mono">{commodity}</span>.
      </div>
    );
  }

  if (!quote && !error) {
    return (
      <div className="rounded-md bg-surface2 px-3 py-2 text-xs text-gray-400">
        {loading ? "Loading market price…" : "—"}
      </div>
    );
  }

  if (error && !quote) {
    return (
      <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
        {error}{" "}
        <button onClick={() => fetchQuote(true)} className="underline">
          retry
        </button>
      </div>
    );
  }

  if (!quote) return null;

  const priceMt = quote.price_mt;
  const hasMt = priceMt !== null && priceMt !== undefined;
  const change = quote.change_pct;
  const changeColor =
    change === null || change === undefined
      ? "text-gray-400"
      : change >= 0
        ? "text-success"
        : "text-danger";
  const changeArrow =
    change === null || change === undefined ? "" : change >= 0 ? "▲" : "▼";

  const sourceLabel = SOURCE_LABELS[quote.source] ?? quote.source;

  const buyDeviation =
    hasMt && buyPrice && priceMt && priceMt > 0
      ? ((buyPrice - priceMt) / priceMt) * 100
      : null;
  const sellDeviation =
    hasMt && sellPrice && priceMt && priceMt > 0
      ? ((sellPrice - priceMt) / priceMt) * 100
      : null;

  return (
    <div
      className={`rounded-md bg-surface2 ${compact ? "px-3 py-2" : "p-4"}`}
      data-testid="price-display"
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-500">
            {quote.display} ({quote.exchange})
          </div>
          <div className="mt-1 flex flex-wrap items-baseline gap-2">
            <span className="text-xl font-semibold">
              {hasMt
                ? `${formatMoney(priceMt, quote.currency)}/MT`
                : `${formatMoney(quote.raw_price, quote.currency)} ${quote.quoted_unit.split("/")[1] ? `/ ${quote.quoted_unit.split("/")[1]}` : ""}`}
            </span>
            {change !== null && change !== undefined && (
              <span className={`text-xs ${changeColor}`}>
                {changeArrow} {Math.abs(change).toFixed(2)}%
              </span>
            )}
            {quote.stale && (
              <span
                className="rounded bg-yellow-400/15 px-1.5 py-0.5 text-[10px] text-yellow-400"
                title="Every price source failed — showing the last known good quote"
              >
                delayed
              </span>
            )}
          </div>
          <div className="mt-0.5 text-[10px] text-gray-500">
            <span className="font-mono">{quote.ticker}</span> ·{" "}
            {hasMt
              ? `native ${quote.raw_price.toFixed(2)} ${quote.quoted_unit}`
              : quote.quoted_unit}
          </div>
        </div>
        <button
          onClick={() => fetchQuote(true)}
          className="text-[10px] text-gray-500 hover:text-gray-300"
          disabled={loading}
          title="Refresh now (bypass 5-min cache)"
        >
          {loading ? "…" : "↻"}
        </button>
      </div>

      {(buyDeviation !== null || sellDeviation !== null) && (
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
          {buyDeviation !== null && (
            <div className="rounded bg-surface px-2 py-1.5">
              <div className="text-[10px] uppercase text-gray-500">vs buy</div>
              <div className={deviationClass(buyDeviation)}>
                {buyDeviation >= 0 ? "+" : ""}
                {buyDeviation.toFixed(1)}%
              </div>
            </div>
          )}
          {sellDeviation !== null && (
            <div className="rounded bg-surface px-2 py-1.5">
              <div className="text-[10px] uppercase text-gray-500">vs sell</div>
              <div className={deviationClass(sellDeviation)}>
                {sellDeviation >= 0 ? "+" : ""}
                {sellDeviation.toFixed(1)}%
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-2 text-[10px] text-gray-500">
        Last updated: {formatTimestamp(quote.timestamp)} · {sourceLabel} (reference only)
      </div>
    </div>
  );
}
