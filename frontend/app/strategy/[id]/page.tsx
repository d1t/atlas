"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../../components/AppShell";
import {
  api,
  PillarKey,
  PillarProgress,
  StrategyBoard,
  StrategyTask,
} from "../../../lib/api";

const PILLAR_ORDER: PillarKey[] = [
  "origination",
  "demand",
  "supply",
  "execution",
];

const STATUS_TONE: Record<PillarProgress["status"], string> = {
  on_track: "text-green-300 border-green-500/40",
  at_risk: "text-amber-300 border-amber-500/40",
  behind: "text-red-300 border-red-500/40",
  idle: "text-gray-400 border-border",
};

const PRIORITY_TONE: Record<string, string> = {
  high: "bg-red-900/40 text-red-200",
  medium: "bg-amber-900/30 text-amber-200",
  low: "bg-gray-700/40 text-gray-300",
};

export default function StrategyBoardPage() {
  const params = useParams();
  const router = useRouter();
  const id = Number(params?.id);

  const [board, setBoard] = useState<StrategyBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      setBoard(await api.getStrategyBoard(id));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function generatePlan() {
    setBusy(true);
    setError(null);
    try {
      await api.generatePlan(id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function replanPillars() {
    setBusy(true);
    setError(null);
    try {
      await api.replanPillars(id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function syncReplies() {
    setBusy(true);
    setError(null);
    setSyncMsg(null);
    try {
      const res = await api.syncReplies();
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
          } · ${res.matched} matched to leads. Pillars updated.`,
        );
      }
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleTask(task: StrategyTask) {
    const next = task.status === "done" ? "todo" : "done";
    try {
      await api.updateStrategyTask(id, task.id, { status: next });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (loading && !board) {
    return (
      <AppShell>
        <div className="text-gray-400">Loading strategy…</div>
      </AppShell>
    );
  }

  if (!board) {
    return (
      <AppShell>
        <div className="text-red-300">{error || "Strategy not found"}</div>
      </AppShell>
    );
  }

  const s = board.strategy;
  const weekTasksByPillar = (pillar: PillarKey) =>
    board.week_tasks.filter((t) => t.pillar === pillar);

  return (
    <AppShell>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <button
            onClick={() => router.push("/strategy")}
            className="text-sm text-gray-400 hover:text-gray-200"
          >
            ← All strategies
          </button>
          <h1 className="mt-1 text-2xl font-semibold">{s.title}</h1>
          {s.north_star && (
            <p className="mt-1 max-w-2xl text-sm text-gray-300">{s.north_star}</p>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <button
            className="btn-primary"
            onClick={generatePlan}
            disabled={busy}
          >
            {busy ? "Working…" : "Generate this week's plan"}
          </button>
          <button className="btn-ghost" onClick={syncReplies} disabled={busy}>
            Sync replies
          </button>
          <button className="btn-ghost" onClick={replanPillars} disabled={busy}>
            Re-plan pillars (AI)
          </button>
        </div>
      </div>

      {syncMsg && (
        <div className="mb-3 rounded-md border border-accent/30 bg-accent/10 p-3 text-sm text-gray-200">
          {syncMsg}
        </div>
      )}

      {error && (
        <div className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="card mb-4 border-accent/30 bg-accent/5">
        <div className="text-xs uppercase tracking-wide text-gray-400">
          Week of {board.week_start}
        </div>
        <div className="mt-1 text-lg font-medium">{board.headline}</div>
      </div>

      {/* Pillars */}
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-400">
        Value-chain pillars
      </h2>
      <div className="mb-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {PILLAR_ORDER.map((key) => {
          const p = board.pillars.find((x) => x.pillar === key);
          if (!p) return null;
          return (
            <div key={key} className={`card border ${STATUS_TONE[p.status]}`}>
              <div className="flex items-center justify-between">
                <div className="font-semibold">{p.label}</div>
                <span className="text-xs capitalize">
                  {p.status.replace("_", " ")}
                </span>
              </div>
              {p.objective && (
                <p className="mt-1 text-xs text-gray-400">{p.objective}</p>
              )}
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-gray-400">
                  <span>{p.kpi}</span>
                  <span>
                    {p.actual}/{p.target}
                  </span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded bg-surface2">
                  <div
                    className="h-full rounded bg-accent"
                    style={{ width: `${Math.min(100, p.progress_pct)}%` }}
                  />
                </div>
              </div>
              <div className="mt-2 text-xs text-gray-500">
                {p.tasks_done}/{p.tasks_total} tasks done this week
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        {/* Today */}
        <div>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-400">
            Today&apos;s focus
          </h2>
          <div className="card space-y-2">
            {board.today_tasks.length === 0 && (
              <div className="text-sm text-gray-500">
                Nothing queued for today. Generate a plan to populate the
                cadence.
              </div>
            )}
            {board.today_tasks.map((t) => (
              <TaskRow key={t.id} task={t} onToggle={() => toggleTask(t)} />
            ))}
          </div>
        </div>

        {/* This week by pillar */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              This week&apos;s cadence
            </h2>
            <AddTaskInline id={id} onAdded={load} />
          </div>
          <div className="space-y-4">
            {PILLAR_ORDER.map((key) => {
              const tasks = weekTasksByPillar(key);
              if (tasks.length === 0) return null;
              const label = board.pillars.find((p) => p.pillar === key)?.label;
              return (
                <div key={key} className="card">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-accent">
                    {label}
                  </div>
                  <div className="space-y-2">
                    {tasks.map((t) => (
                      <TaskRow
                        key={t.id}
                        task={t}
                        onToggle={() => toggleTask(t)}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
            {board.week_tasks.length === 0 && (
              <div className="card text-sm text-gray-500">
                No tasks yet. Click &quot;Generate this week&apos;s plan&quot; to
                build the cadence from your live opportunities.
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function TaskRow({
  task,
  onToggle,
}: {
  task: StrategyTask;
  onToggle: () => void;
}) {
  const done = task.status === "done";
  return (
    <div className="flex items-start gap-2">
      <input
        type="checkbox"
        checked={done}
        onChange={onToggle}
        className="mt-1"
      />
      <div className="flex-1">
        <div
          className={`text-sm ${done ? "text-gray-500 line-through" : "text-gray-200"}`}
        >
          {task.title}
        </div>
        {task.detail && (
          <div className="text-xs text-gray-500">{task.detail}</div>
        )}
      </div>
      <span
        className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${PRIORITY_TONE[task.priority] || ""}`}
      >
        {task.priority}
      </span>
    </div>
  );
}

function AddTaskInline({ id, onAdded }: { id: number; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [pillar, setPillar] = useState<PillarKey>("origination");
  const [busy, setBusy] = useState(false);

  async function add() {
    if (!title.trim()) return;
    setBusy(true);
    try {
      await api.createStrategyTask(id, { pillar, title: title.trim() });
      setTitle("");
      setOpen(false);
      onAdded();
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="btn-ghost text-xs" onClick={() => setOpen(true)}>
        + Add task
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1">
      <select
        className="input py-1 text-xs"
        value={pillar}
        onChange={(e) => setPillar(e.target.value as PillarKey)}
      >
        {PILLAR_ORDER.map((k) => (
          <option key={k} value={k}>
            {k}
          </option>
        ))}
      </select>
      <input
        className="input py-1 text-xs"
        placeholder="Task title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && add()}
      />
      <button className="btn-primary text-xs" onClick={add} disabled={busy}>
        Add
      </button>
      <button className="btn-ghost text-xs" onClick={() => setOpen(false)}>
        ✕
      </button>
    </div>
  );
}
