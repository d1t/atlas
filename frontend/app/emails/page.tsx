"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import {
  api,
  EmailMessage,
  GmailStatus,
  PILLAR_LABELS,
  PillarKey,
  Strategy,
  StrategyBoard,
  StrategyTask,
  TaskEmailDraft,
} from "../../lib/api";

const PRIORITY_TONE: Record<string, string> = {
  high: "bg-red-900/40 text-red-200",
  medium: "bg-amber-900/30 text-amber-200",
  low: "bg-gray-700/40 text-gray-300",
};

const PRIORITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

export default function EmailsPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategyId, setStrategyId] = useState<number | null>(null);
  const [board, setBoard] = useState<StrategyBoard | null>(null);
  const [gmail, setGmail] = useState<GmailStatus | null>(null);
  const [outbox, setOutbox] = useState<EmailMessage[]>([]);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [composer, setComposer] = useState<{
    task: StrategyTask;
    draft: TaskEmailDraft;
  } | null>(null);

  const loadOutbox = useCallback(async () => {
    try {
      const rows = await api.listEmails();
      setOutbox(rows.filter((m) => m.direction === "outbound").slice(0, 25));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const loadBoard = useCallback(async (sid: number) => {
    try {
      setBoard(await api.getStrategyBoard(sid));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [list, status] = await Promise.all([
          api.listStrategies(),
          api.gmailStatus(),
        ]);
        setStrategies(list);
        setGmail(status);
        const active = list.find((s) => s.status === "active") || list[0];
        if (active) setStrategyId(active.id);
      } catch (e) {
        setError((e as Error).message);
      }
      await loadOutbox();
    })();
  }, [loadOutbox]);

  useEffect(() => {
    if (strategyId) loadBoard(strategyId);
  }, [strategyId, loadBoard]);

  const queue = useMemo(() => {
    if (!board) return [];
    return board.week_tasks
      .filter((t) => t.status === "todo" || t.status === "doing")
      .sort(
        (a, b) =>
          (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3) ||
          a.id - b.id,
      );
  }, [board]);

  const doneCount = board
    ? board.week_tasks.filter((t) => t.status === "done").length
    : 0;

  async function openDraft(task: StrategyTask) {
    if (!strategyId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const draft = await api.draftTaskEmail(strategyId, task.id);
      setComposer({ task, draft });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleSent(sentTaskId: number, summary: string) {
    setNotice(summary);
    setComposer(null);
    if (strategyId) await loadBoard(strategyId);
    await loadOutbox();
    // Execution flow: jump straight to the next pending task.
    const next = queue.find((t) => t.id !== sentTaskId);
    if (next) await openDraft(next);
  }

  async function toggleTask(task: StrategyTask) {
    if (!strategyId) return;
    const next = task.status === "done" ? "todo" : "done";
    try {
      await api.updateStrategyTask(strategyId, task.id, { status: next });
      await loadBoard(strategyId);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function syncReplies() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.syncReplies();
      if (res.mode === "offline") {
        setNotice(
          "Gmail is offline (no credentials) — nothing to sync. Add a Gmail App Password to pull live replies.",
        );
      } else if (res.new_messages.length === 0) {
        setNotice(`Checked inbox (${res.fetched} scanned) — no new replies.`);
      } else {
        setNotice(
          `Synced ${res.new_messages.length} new repl${
            res.new_messages.length === 1 ? "y" : "ies"
          } · ${res.matched} matched to leads.`,
        );
      }
      if (strategyId) await loadBoard(strategyId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Emails</h1>
          <p className="text-sm text-gray-400">
            Work the plan one email at a time — draft, review, send, and tick the
            task off. Each send is logged to the lead it concerns.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {gmail && (
            <span
              className={`badge ${
                gmail.mode === "live"
                  ? "border-green-500/40 text-green-300"
                  : "border-amber-500/40 text-amber-300"
              }`}
              title={
                gmail.mode === "live"
                  ? `Sending live as ${gmail.address}`
                  : "No Gmail credentials — emails are recorded but not transmitted"
              }
            >
              Gmail: {gmail.mode === "live" ? `Live (${gmail.address})` : "Offline"}
            </span>
          )}
          <button className="btn-ghost" onClick={syncReplies} disabled={busy}>
            Sync replies
          </button>
        </div>
      </div>

      {strategies.length > 1 && (
        <div className="mb-3">
          <label className="text-xs text-gray-400">Strategy</label>
          <select
            className="input ml-2 py-1 text-sm"
            value={strategyId ?? ""}
            onChange={(e) => setStrategyId(Number(e.target.value))}
          >
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </div>
      )}

      {notice && (
        <div className="mb-3 rounded-md border border-accent/30 bg-accent/10 p-3 text-sm text-gray-200">
          {notice}
        </div>
      )}
      {error && (
        <div className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {strategies.length === 0 && (
        <div className="card text-sm text-gray-500">
          No strategy yet. Create one under{" "}
          <a href="/strategy" className="text-accent">
            Strategy
          </a>{" "}
          and generate a weekly plan — its tasks become your email queue here.
        </div>
      )}

      {strategies.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
          {/* Execution queue */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
                Execution queue{board ? ` — week of ${board.week_start}` : ""}
              </h2>
              <span className="text-xs text-gray-500">
                {queue.length} to do · {doneCount} done
              </span>
            </div>
            <div className="card space-y-2">
              {queue.length === 0 && (
                <div className="text-sm text-gray-500">
                  Nothing queued. Generate this week&apos;s plan on the{" "}
                  <a href={`/strategy/${strategyId ?? ""}`} className="text-accent">
                    strategy board
                  </a>{" "}
                  to populate your email cadence.
                </div>
              )}
              {queue.map((t) => (
                <QueueRow
                  key={t.id}
                  task={t}
                  disabled={busy}
                  onDraft={() => openDraft(t)}
                  onToggle={() => toggleTask(t)}
                />
              ))}
            </div>
          </div>

          {/* Outbox */}
          <div>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-400">
              Sent &amp; recorded
            </h2>
            <div className="card space-y-2">
              {outbox.length === 0 && (
                <div className="text-sm text-gray-500">
                  No emails yet. Draft and send from the queue to see them here.
                </div>
              )}
              {outbox.map((m) => (
                <div key={m.id} className="border-b border-border pb-2 last:border-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="truncate text-sm text-gray-200">
                      {m.subject || "(no subject)"}
                    </div>
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase ${
                        m.status === "sent"
                          ? "bg-green-900/40 text-green-200"
                          : m.status === "offline"
                            ? "bg-amber-900/30 text-amber-200"
                            : "bg-red-900/40 text-red-200"
                      }`}
                    >
                      {m.status}
                    </span>
                  </div>
                  <div className="text-xs text-gray-500">
                    → {m.to_email || "—"}
                    {m.created_at
                      ? ` · ${new Date(m.created_at).toLocaleString()}`
                      : ""}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {composer && strategyId && (
        <Composer
          strategyId={strategyId}
          task={composer.task}
          draft={composer.draft}
          onClose={() => setComposer(null)}
          onSent={handleSent}
        />
      )}
    </AppShell>
  );
}

function QueueRow({
  task,
  disabled,
  onDraft,
  onToggle,
}: {
  task: StrategyTask;
  disabled: boolean;
  onDraft: () => void;
  onToggle: () => void;
}) {
  const linked =
    task.supplier_lead_id != null
      ? "supplier"
      : task.buyer_lead_id != null
        ? "buyer"
        : null;
  return (
    <div className="flex items-start gap-2">
      <input
        type="checkbox"
        checked={task.status === "done"}
        onChange={onToggle}
        className="mt-1"
        title="Tick off without emailing"
      />
      <div className="flex-1">
        <div className="text-sm text-gray-200">{task.title}</div>
        {task.detail && (
          <div className="text-xs text-gray-500">{task.detail}</div>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="rounded bg-surface2 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
            {PILLAR_LABELS[task.pillar as PillarKey] ?? task.pillar}
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${PRIORITY_TONE[task.priority] || ""}`}
          >
            {task.priority}
          </span>
          {linked && (
            <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] uppercase text-accent">
              {linked}
            </span>
          )}
        </div>
      </div>
      <button className="btn-primary text-xs" onClick={onDraft} disabled={disabled}>
        Draft email
      </button>
    </div>
  );
}

function Composer({
  strategyId,
  task,
  draft,
  onClose,
  onSent,
}: {
  strategyId: number;
  task: StrategyTask;
  draft: TaskEmailDraft;
  onClose: () => void;
  onSent: (taskId: number, summary: string) => void;
}) {
  const [to, setTo] = useState(draft.to_email ?? "");
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [complete, setComplete] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function send() {
    if (!to.trim()) {
      setErr("Add a recipient email address to send.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await api.sendTaskEmail(strategyId, task.id, {
        to_email: to.trim(),
        subject,
        body,
        complete_task: complete,
      });
      const ticked = res.task.status === "done" ? " · task ticked off" : "";
      const summary =
        res.mode === "offline"
          ? `Recorded offline (no Gmail creds) — would have gone to ${to.trim()}${ticked}.`
          : `Sent to ${to.trim()}${ticked}.`;
      onSent(task.id, summary);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-2xl space-y-3 p-5">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">Draft email</h2>
            <p className="text-xs text-gray-500">{task.title}</p>
          </div>
          <span
            className={`badge ${
              draft.mode === "live"
                ? "border-green-500/40 text-green-300"
                : "border-amber-500/40 text-amber-300"
            }`}
          >
            {draft.mode === "live" ? "Will send live" : "Offline — records only"}
          </span>
        </div>

        {draft.reason && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
            {draft.reason}
          </div>
        )}

        <label className="block text-sm">
          <span className="text-gray-400">To</span>
          <input
            className="input mt-1 w-full"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="recipient@company.com"
          />
        </label>
        <label className="block text-sm">
          <span className="text-gray-400">Subject</span>
          <input
            className="input mt-1 w-full"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </label>
        <label className="block text-sm">
          <span className="text-gray-400">Body</span>
          <textarea
            className="input mt-1 w-full font-mono text-xs"
            rows={14}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>

        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={complete}
            onChange={(e) => setComplete(e.target.checked)}
          />
          Tick this task off after sending
        </label>

        {err && <div className="text-sm text-red-400">{err}</div>}

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn-primary" onClick={send} disabled={busy}>
            {busy ? "Sending…" : complete ? "Send & tick off" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
