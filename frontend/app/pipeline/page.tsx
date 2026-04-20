"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { api, Deal, PipelineBoard, STAGE_LABELS } from "../../lib/api";
import { money, numberShort } from "../../lib/format";

export default function PipelinePage() {
  const [board, setBoard] = useState<PipelineBoard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);

  async function refresh() {
    try {
      setBoard(await api.pipelineBoard());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load board");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function drop(stage: string) {
    if (dragging === null) return;
    try {
      await api.changeStage(dragging, stage);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid transition");
    } finally {
      setDragging(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-2xl font-semibold">Pipeline</h1>
        <p className="text-sm text-gray-400">
          Drag deals across stages. Invalid transitions are rejected by the server.
        </p>
      </div>

      {error && (
        <div className="card mb-4 border-danger text-sm text-danger">{error}</div>
      )}

      {!board ? (
        <p className="text-gray-400">Loading…</p>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4">
          {board.stages.map((stage) => {
            const items = board.columns[stage] || [];
            return (
              <div
                key={stage}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => drop(stage)}
                className="min-w-[260px] flex-1 rounded-lg border border-border bg-surface p-3"
              >
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-semibold">
                    {STAGE_LABELS[stage] || stage}
                  </h3>
                  <span className="badge">{items.length}</span>
                </div>
                <div className="space-y-2">
                  {items.map((d: Deal) => (
                    <Link
                      key={d.id}
                      href={`/deals/${d.id}`}
                      draggable
                      onDragStart={() => setDragging(d.id)}
                      onDragEnd={() => setDragging(null)}
                      className="block rounded-md bg-surface2 p-2 hover:bg-border"
                    >
                      <div className="text-sm font-medium text-gray-100">
                        {d.title}
                      </div>
                      <div className="mt-1 flex items-center justify-between text-xs text-gray-400">
                        <span>
                          {numberShort(d.volume_mt)} MT · {d.commodity}
                        </span>
                        <span
                          className={
                            d.total_margin >= 0 ? "text-success" : "text-danger"
                          }
                        >
                          {money(d.total_margin, d.currency)}
                        </span>
                      </div>
                    </Link>
                  ))}
                  {items.length === 0 && (
                    <p className="py-4 text-center text-xs text-gray-500">
                      Drop here
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </AppShell>
  );
}
