"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell } from "../../../components/AppShell";
import {
  api,
  ConnectionStatus,
  ScopeExplanation,
} from "../../../lib/api";

type Notice = { kind: "ok" | "err" | "info"; text: string };

const MODE_LABEL: Record<ConnectionStatus["mode"], string> = {
  live: "Connected",
  offline: "Not connected",
  needs_reconnect: "Action needed",
  unavailable: "Unavailable",
};

const MODE_STYLE: Record<ConnectionStatus["mode"], string> = {
  live: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  offline: "bg-gray-500/10 text-gray-400 border-gray-500/30",
  needs_reconnect: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  unavailable: "bg-gray-500/10 text-gray-500 border-gray-500/20",
};

function scopeLabel(scope: string): string {
  if (scope.endsWith("gmail.send")) return "Send email";
  if (scope.endsWith("gmail.readonly")) return "Read email";
  if (scope === "openid" || scope === "email") return "Identify your account";
  return scope;
}

function IntegrationsPanel() {
  const params = useSearchParams();
  const [status, setStatus] = useState<ConnectionStatus | null>(null);
  const [scopes, setScopes] = useState<ScopeExplanation[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, p] = await Promise.all([
        api.integrationStatus(),
        api.integrationPermissions(),
      ]);
      setStatus(s);
      setScopes(p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // The OAuth callback returns the browser here with the outcome in the query
  // string, since it cannot render anything itself.
  useEffect(() => {
    const outcome = params.get("gmail");
    if (!outcome) return;
    const message = params.get("message");
    if (outcome === "connected") {
      setNotice({ kind: "ok", text: "Gmail connected." });
    } else if (outcome === "cancelled") {
      setNotice({
        kind: "info",
        text: "Connection cancelled — nothing was changed.",
      });
    } else {
      setNotice({
        kind: "err",
        text: message ? decodeURIComponent(message) : "Could not connect Gmail.",
      });
    }
  }, [params]);

  async function connect() {
    setBusy(true);
    setNotice(null);
    try {
      const { authorization_url } = await api.connectGmail();
      window.location.href = authorization_url;
    } catch (e) {
      setNotice({ kind: "err", text: (e as Error).message });
      setBusy(false);
    }
  }

  async function disconnect() {
    if (
      !window.confirm(
        "Disconnect Gmail? Atlas will stop sending from this mailbox and will " +
          "revoke its access at Google. Emails already sent are unaffected.",
      )
    )
      return;
    setBusy(true);
    setNotice(null);
    try {
      await api.disconnectGmail();
      setNotice({ kind: "ok", text: "Gmail disconnected." });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Integrations</h1>
        <p className="text-sm text-gray-400">
          Connect the mailbox Atlas sends approved outreach from and watches for
          replies.
        </p>
      </div>

      {notice && (
        <div
          className={`mb-4 rounded border px-3 py-2 text-sm ${
            notice.kind === "ok"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
              : notice.kind === "err"
                ? "border-red-500/30 bg-red-500/10 text-red-300"
                : "border-gray-500/30 bg-gray-500/10 text-gray-300"
          }`}
        >
          {notice.text}
        </div>
      )}

      {loading ? (
        <div className="card max-w-2xl animate-pulse space-y-3">
          <div className="h-4 w-40 rounded bg-gray-700" />
          <div className="h-3 w-64 rounded bg-gray-800" />
        </div>
      ) : error ? (
        <div className="card max-w-2xl">
          <p className="text-sm text-red-400">{error}</p>
          <button className="btn mt-3" onClick={load}>
            Try again
          </button>
        </div>
      ) : status ? (
        <div className="card max-w-2xl space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-medium">Gmail</h2>
              <p className="mt-1 text-sm text-gray-400">{status.detail}</p>
              {status.address && (
                <p className="mt-1 text-sm text-gray-300">{status.address}</p>
              )}
            </div>
            <span
              className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${MODE_STYLE[status.mode]}`}
            >
              {MODE_LABEL[status.mode]}
            </span>
          </div>

          {status.mode === "needs_reconnect" &&
            status.missing_scopes.length > 0 && (
              <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-200">
                <p className="font-medium">Missing permission</p>
                <ul className="mt-1 list-disc pl-5 text-amber-200/80">
                  {status.missing_scopes.map((s) => (
                    <li key={s}>{scopeLabel(s)}</li>
                  ))}
                </ul>
                <p className="mt-2 text-amber-200/80">
                  Reconnect and accept every requested permission — Atlas cannot
                  send without them.
                </p>
              </div>
            )}

          {status.provider === "smtp" ? (
            <p className="rounded border border-gray-700 bg-gray-800/40 p-3 text-sm text-gray-400">
              This deployment uses a shared mailbox configured by an
              administrator. It is a development and administrator fallback, so
              there is nothing to connect here.
            </p>
          ) : status.mode === "unavailable" ? (
            <p className="rounded border border-gray-700 bg-gray-800/40 p-3 text-sm text-gray-400">
              Gmail sign-in has not been set up on this deployment yet. Drafts
              are still written and recorded — they are just not transmitted.
            </p>
          ) : (
            <>
              <div>
                <h3 className="text-sm font-medium">
                  What Atlas will ask Google for
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  Only these. Atlas never modifies or deletes anything in your
                  mailbox.
                </p>
                <ul className="mt-2 space-y-2">
                  {scopes.map((s) => (
                    <li key={s.scope} className="text-sm">
                      <span className="text-gray-200">
                        {scopeLabel(s.scope)}
                      </span>
                      <span className="block text-gray-500">{s.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  className="btn btn-primary"
                  onClick={connect}
                  disabled={busy}
                >
                  {busy
                    ? "Opening Google…"
                    : status.connected
                      ? "Reconnect"
                      : "Connect Gmail"}
                </button>
                {status.connected && (
                  <button className="btn" onClick={disconnect} disabled={busy}>
                    Disconnect
                  </button>
                )}
              </div>
            </>
          )}

          {(status.connected_at || status.last_used_at) && (
            <dl className="grid grid-cols-2 gap-2 border-t border-gray-800 pt-4 text-xs text-gray-500">
              {status.connected_at && (
                <div>
                  <dt>Connected</dt>
                  <dd className="text-gray-400">
                    {new Date(status.connected_at).toLocaleString()}
                  </dd>
                </div>
              )}
              {status.last_used_at && (
                <div>
                  <dt>Last used</dt>
                  <dd className="text-gray-400">
                    {new Date(status.last_used_at).toLocaleString()}
                  </dd>
                </div>
              )}
            </dl>
          )}
        </div>
      ) : null}
    </>
  );
}

export default function IntegrationsPage() {
  // useSearchParams needs a boundary: the OAuth outcome is only known in the
  // browser, so this page cannot be prerendered.
  return (
    <AppShell>
      <Suspense
        fallback={
          <div className="card max-w-2xl animate-pulse space-y-3">
            <div className="h-4 w-40 rounded bg-gray-700" />
            <div className="h-3 w-64 rounded bg-gray-800" />
          </div>
        }
      >
        <IntegrationsPanel />
      </Suspense>
    </AppShell>
  );
}
