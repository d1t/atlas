"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../../../components/AppShell";
import {
  AgentActionOut,
  AgentRunOut,
  api,
  ApprovalQueueItem,
  AuditLogOut,
  EvidenceOut,
  ExecutionRunOut,
  Strategy,
  TaskNode,
} from "../../../../lib/api";

const STATE_TONE: Record<string, string> = {
  completed: "bg-green-900/40 text-green-200 border-green-500/30",
  waiting_response: "bg-sky-900/40 text-sky-200 border-sky-500/30",
  waiting_human: "bg-amber-900/30 text-amber-200 border-amber-500/30",
  awaiting_approval: "bg-amber-900/40 text-amber-200 border-amber-500/40",
  queued: "bg-gray-700/40 text-gray-200 border-border",
  in_progress: "bg-sky-900/30 text-sky-200 border-sky-500/30",
  blocked: "bg-red-900/30 text-red-200 border-red-500/30",
  failed: "bg-red-900/40 text-red-200 border-red-500/40",
  rejected: "bg-gray-700/40 text-gray-400 border-border",
  cancelled: "bg-gray-700/40 text-gray-400 border-border",
};

const RISK_TONE: Record<string, string> = {
  high: "bg-red-900/40 text-red-200",
  medium: "bg-amber-900/30 text-amber-200",
  low: "bg-gray-700/40 text-gray-300",
};

function tone(state: string) {
  return STATE_TONE[state] || "bg-gray-700/40 text-gray-300 border-border";
}

function label(value: string) {
  return value.replace(/_/g, " ");
}

function when(value: string | null) {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function StrategyExecutionPage() {
  const params = useParams();
  const id = Number(params?.id);

  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [tree, setTree] = useState<TaskNode[]>([]);
  const [runs, setRuns] = useState<AgentRunOut[]>([]);
  const [actions, setActions] = useState<AgentActionOut[]>([]);
  const [queue, setQueue] = useState<ApprovalQueueItem[]>([]);
  const [audit, setAudit] = useState<AuditLogOut[]>([]);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [s, t, r, a, q, l] = await Promise.all([
        api.getStrategy(id),
        api.taskTree(id),
        api.agentRuns(id),
        api.agentActions(id),
        api.approvalQueue(id),
        api.auditLog(id),
      ]);
      setStrategy(s);
      setTree(t);
      setRuns(r);
      setActions(a);
      setQueue(q);
      setAudit(l);
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

  async function act(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const plan = () =>
    act("plan", async () => {
      const res = await api.planStrategy(id);
      setNotice(
        res.created_task_ids.length === 0
          ? "No new tasks — the agents found no gap that isn't already being worked."
          : `${res.created_task_ids.length} task(s) planned. ${res.run.reasoning ?? ""}`,
      );
    });

  const execute = () =>
    act("execute", async () => {
      const res: ExecutionRunOut = await api.executeStrategy(id);
      setNotice(summariseRun(res));
    });

  const resume = () =>
    act("resume", async () => {
      const woken = await api.resumeStrategy(id);
      setNotice(
        woken.length === 0
          ? "No replies have arrived for the actions that are waiting."
          : `${woken.length} action(s) resumed on a reply.`,
      );
    });

  const togglePause = () =>
    act("pause", async () => {
      const next = !strategy?.agents_paused;
      const res = await api.setAgentPause(
        id,
        next,
        next ? "Paused from the execution view." : undefined,
      );
      setNotice(
        res.agents_paused
          ? "Agents paused. Nothing will be sent until you resume them."
          : "Agents resumed.",
      );
    });

  if (loading) {
    return (
      <AppShell>
        <div className="text-gray-400">Loading execution view…</div>
      </AppShell>
    );
  }

  if (!strategy) {
    return (
      <AppShell>
        <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error || "Strategy not found."}
        </div>
      </AppShell>
    );
  }

  const paused = strategy.agents_paused;
  const flat = flatten(tree);
  const blocked = flat.filter((t) => t.blocked_reason || t.blocked_by.length > 0);
  const waiting = actions.filter(
    (a) => a.state === "waiting_response" || a.state === "waiting_human",
  );
  const done = flat.filter((t) => t.status === "done").length;
  const gated = flat.filter((t) => t.requires_evidence);
  const evidenced = gated.filter((t) => t.evidence_count > 0).length;

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            href={`/strategy/${id}`}
            className="text-sm text-gray-400 hover:text-gray-200"
          >
            ← Weekly board
          </Link>
          <h1 className="mt-1 text-2xl font-semibold">{strategy.title}</h1>
          <p className="text-sm text-gray-400">
            What the agents have done, what they are waiting on, and what needs
            you.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={plan} disabled={!!busy}>
            {busy === "plan" ? "Planning…" : "Plan"}
          </button>
          <button
            className="btn-primary"
            onClick={execute}
            disabled={!!busy || paused}
            title={paused ? "Agents are paused" : undefined}
          >
            {busy === "execute" ? "Running…" : "Run agents"}
          </button>
          <button className="btn-ghost" onClick={resume} disabled={!!busy}>
            Check for replies
          </button>
          <button
            className={paused ? "btn-primary" : "btn-ghost"}
            onClick={togglePause}
            disabled={!!busy}
          >
            {paused ? "Resume agents" : "Pause agents"}
          </button>
        </div>
      </div>

      {paused && (
        <div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-200">
          <strong>Agents are paused.</strong>{" "}
          {strategy.agents_paused_reason ||
            "Nothing will be sent until you resume them."}{" "}
          Approvals you grant are kept and will run once you resume.
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

      {/* Health */}
      <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Needs your approval"
          value={queue.length}
          detail={
            queue.length === 0
              ? "Nothing waiting on you."
              : "Agents have prepared these and stopped."
          }
          tone={queue.length > 0 ? "amber" : "plain"}
        />
        <Stat
          label="Waiting on a counterparty"
          value={waiting.length}
          detail="Sent, or drafted for you to review."
        />
        <Stat
          label="Blocked"
          value={blocked.length}
          detail={
            blocked.length === 0
              ? "Nothing stuck."
              : "Missing an input, or waiting on another task."
          }
          tone={blocked.length > 0 ? "red" : "plain"}
        />
        <Stat
          label="Evidenced outcomes"
          value={`${evidenced}/${gated.length}`}
          detail="Gated tasks with proof on file."
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_1fr]">
        <div className="space-y-6">
          <ApprovalQueue
            items={queue}
            busy={busy}
            paused={paused}
            onDecide={(approvalId, approved, reason) =>
              act(`approval-${approvalId}`, async () => {
                await api.decideApproval(approvalId, approved, reason);
                setNotice(
                  approved
                    ? paused
                      ? "Approved. It will send when you resume the agents."
                      : "Approved and sent. The agent is now waiting for a reply."
                    : "Rejected. The agent will not send it.",
                );
              })
            }
          />

          <section>
            <SectionTitle>
              Task tree
              <span className="ml-2 font-normal normal-case text-gray-500">
                {done}/{flat.length} done
              </span>
            </SectionTitle>
            {tree.length === 0 ? (
              <Empty>
                No agent plan yet. Click <strong>Plan</strong> to decompose the
                strategy&apos;s gaps into tasks.
              </Empty>
            ) : (
              <div className="card space-y-1">
                {tree.map((node) => (
                  <TaskBranch
                    key={node.id}
                    node={node}
                    depth={0}
                    actions={actions}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        <div className="space-y-6">
          <section>
            <SectionTitle>Agent activity</SectionTitle>
            {runs.length === 0 ? (
              <Empty>No agent has run against this strategy yet.</Empty>
            ) : (
              <div className="card space-y-3">
                {runs.slice(0, 6).map((run) => (
                  <div key={run.id} className="border-b border-border pb-3 last:border-0 last:pb-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium capitalize">
                        {label(run.agent_key)}
                      </span>
                      <span className="text-xs text-gray-500">
                        {when(run.finished_at || run.started_at || run.created_at)}
                      </span>
                    </div>
                    {run.summary && (
                      <div className="mt-1 text-sm text-gray-300">{run.summary}</div>
                    )}
                    {run.reasoning && (
                      <p className="mt-1 text-xs leading-relaxed text-gray-500">
                        {run.reasoning}
                      </p>
                    )}
                    {run.error && (
                      <p className="mt-1 text-xs text-red-300">{run.error}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          {blocked.length > 0 && (
            <section>
              <SectionTitle>Blockers</SectionTitle>
              <div className="card space-y-2">
                {blocked.map((t) => (
                  <div key={t.id} className="text-sm">
                    <div className="text-gray-200">{t.title}</div>
                    <div className="text-xs text-red-300">
                      {t.blocked_reason ||
                        `Waiting on ${t.blocked_by.length} earlier task(s).`}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section>
            <SectionTitle>Execution history</SectionTitle>
            {audit.length === 0 ? (
              <Empty>Nothing has happened yet.</Empty>
            ) : (
              <div className="card space-y-1">
                {audit.slice(0, 25).map((entry) => (
                  <div
                    key={entry.id}
                    className="flex items-baseline justify-between gap-2 text-xs"
                  >
                    <span className="text-gray-300">
                      <span
                        className={
                          entry.actor_type === "human"
                            ? "text-accent"
                            : "text-gray-500"
                        }
                      >
                        {entry.actor_label || entry.actor_type}
                      </span>{" "}
                      {label(entry.action)}
                    </span>
                    <span className="shrink-0 text-gray-600">
                      {when(entry.created_at)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
}

function summariseRun(res: ExecutionRunOut): string {
  if (res.outcomes.length === 0) {
    return "Nothing to do — every ready task already has an action in flight.";
  }
  const counts = new Map<string, number>();
  res.outcomes.forEach((o) => counts.set(o.state, (counts.get(o.state) || 0) + 1));
  const parts = [...counts.entries()].map(([s, n]) => `${n} ${label(s)}`);
  const gatedCount = counts.get("awaiting_approval") || 0;
  return (
    `${parts.join(", ")}.` +
    (gatedCount > 0
      ? ` Nothing was sent — ${gatedCount} message(s) need your approval first.`
      : "")
  );
}

function flatten(nodes: TaskNode[]): TaskNode[] {
  return nodes.flatMap((n) => [n, ...flatten(n.children)]);
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-400">
      {children}
    </h2>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="card text-sm text-gray-500">{children}</div>;
}

function Stat({
  label: title,
  value,
  detail,
  tone: t = "plain",
}: {
  label: string;
  value: number | string;
  detail: string;
  tone?: "plain" | "amber" | "red";
}) {
  const border =
    t === "amber"
      ? "border-amber-500/40"
      : t === "red"
        ? "border-red-500/40"
        : "border-border";
  return (
    <div className={`card border ${border}`}>
      <div className="text-xs uppercase tracking-wide text-gray-400">{title}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      <div className="mt-1 text-xs text-gray-500">{detail}</div>
    </div>
  );
}

function ApprovalQueue({
  items,
  busy,
  paused,
  onDecide,
}: {
  items: ApprovalQueueItem[];
  busy: string | null;
  paused: boolean;
  onDecide: (approvalId: number, approved: boolean, reason?: string) => void;
}) {
  return (
    <section>
      <SectionTitle>Approval queue</SectionTitle>
      {items.length === 0 ? (
        <Empty>
          Nothing needs your approval. Agents queue a message here whenever it is a
          first approach, quotes a price, or commits to terms.
        </Empty>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <ApprovalCard
              key={item.approval.id}
              item={item}
              busy={busy === `approval-${item.approval.id}`}
              paused={paused}
              onDecide={onDecide}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ApprovalCard({
  item,
  busy,
  paused,
  onDecide,
}: {
  item: ApprovalQueueItem;
  busy: boolean;
  paused: boolean;
  onDecide: (approvalId: number, approved: boolean, reason?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const payload = item.action.payload as Record<string, string | undefined>;
  const body = payload.body || "";

  return (
    <div className="card border border-amber-500/30">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-gray-100">
            {item.task_title || item.action.target}
          </div>
          <div className="mt-0.5 text-xs text-gray-500">
            {label(item.action.action_type)} · to {payload.to_email || item.action.target}
          </div>
        </div>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
            RISK_TONE[item.approval.risk] || ""
          }`}
        >
          {item.approval.risk} risk
        </span>
      </div>

      <p className="mt-2 text-xs text-amber-200">{item.approval.request_summary}</p>

      {payload.subject && (
        <div className="mt-3 text-sm text-gray-200">
          <span className="text-gray-500">Subject:</span> {payload.subject}
        </div>
      )}
      {body && (
        <>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-surface2 p-3 text-xs leading-relaxed text-gray-300">
            {open || body.length <= 420 ? body : `${body.slice(0, 420)}…`}
          </pre>
          {body.length > 420 && (
            <button
              className="mt-1 text-xs text-accent hover:underline"
              onClick={() => setOpen((v) => !v)}
            >
              {open ? "Show less" : "Show the full message"}
            </button>
          )}
        </>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          className="btn-primary text-xs"
          onClick={() => onDecide(item.approval.id, true)}
          disabled={busy}
        >
          {busy ? "Working…" : paused ? "Approve (sends on resume)" : "Approve & send"}
        </button>
        <input
          className="input flex-1 py-1 text-xs"
          placeholder="Reason (optional, recorded either way)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <button
          className="btn-ghost text-xs"
          onClick={() => onDecide(item.approval.id, false, reason.trim() || undefined)}
          disabled={busy}
        >
          Reject
        </button>
      </div>
    </div>
  );
}

function TaskBranch({
  node,
  depth,
  actions,
}: {
  node: TaskNode;
  depth: number;
  actions: AgentActionOut[];
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceOut[] | null>(null);
  const action = actions.find((a) => a.task_id === node.id);
  const done = node.status === "done";

  async function toggleEvidence() {
    setShowEvidence((v) => !v);
    if (evidence === null) {
      try {
        setEvidence(await api.taskEvidence(node.id));
      } catch {
        setEvidence([]);
      }
    }
  }

  return (
    <div style={{ marginLeft: depth * 16 }}>
      <div className="flex flex-wrap items-baseline gap-2 py-1">
        <span className={`text-sm ${done ? "text-gray-500 line-through" : "text-gray-200"}`}>
          {node.title}
        </span>
        {node.assignee === "agent" ? (
          node.capability && (
            <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-gray-400">
              {label(node.capability)}
            </span>
          )
        ) : (
          <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-gray-400">
            you
          </span>
        )}
        {action && (
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] ${tone(action.state)}`}
          >
            {label(action.state)}
          </span>
        )}
        {node.requires_evidence && (
          <button
            onClick={toggleEvidence}
            className={`rounded border px-1.5 py-0.5 text-[10px] ${
              node.evidence_count > 0
                ? "border-green-500/30 bg-green-900/30 text-green-200"
                : "border-border text-gray-500"
            }`}
            title={node.acceptance_criteria || "Evidence required"}
          >
            {node.evidence_count > 0
              ? `${node.evidence_count} evidence`
              : "needs evidence"}
          </button>
        )}
      </div>

      {node.blocked_reason && (
        <div className="pb-1 text-xs text-red-300">{node.blocked_reason}</div>
      )}
      {node.requires_evidence && node.acceptance_criteria && !done && (
        <div className="pb-1 text-xs text-gray-500">
          Done when: {node.acceptance_criteria}
        </div>
      )}
      {showEvidence && (
        <div className="mb-1 rounded border border-border bg-surface2 p-2 text-xs text-gray-400">
          {evidence === null && "Loading evidence…"}
          {evidence?.length === 0 &&
            "No evidence yet. This task cannot be completed until there is some."}
          {evidence?.map((e) => (
            <div key={e.id}>
              <span className="text-gray-500">[{e.kind}]</span> {e.description}
            </div>
          ))}
        </div>
      )}

      {node.children.map((child) => (
        <TaskBranch
          key={child.id}
          node={child}
          depth={depth + 1}
          actions={actions}
        />
      ))}
    </div>
  );
}
