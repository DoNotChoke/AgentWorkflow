"use client";

import Link from "next/link";
import { useState } from "react";
import { StatusPill } from "@/components/run-parts";
import { ErrorNote, Spinner } from "@/components/ui";
import type { RunStatus, RunSummary } from "@/lib/api";
import { useWorkflows } from "@/lib/queries";
import { elapsed, formatDuration, formatStarted, formatTokens, useRuns } from "@/lib/runs";
import { useTenant } from "@/lib/tenant";

const FILTERS: { label: string; status?: RunStatus }[] = [
  { label: "All" },
  { label: "Running", status: "running" },
  { label: "Failed", status: "failed" },
];

/** Mọi lần chạy workflow của workspace đang mở, mới nhất trước. */
export default function RunsPage() {
  const { organization, workspace } = useTenant();
  const workspaceId = workspace?.id ?? null;
  const [filter, setFilter] = useState(0);
  const runs = useRuns(organization.id, workspaceId, { status: FILTERS[filter].status });
  const workflows = useWorkflows(organization.id, workspaceId);

  if (!workspace) return <Spinner label="No workspace in this organisation." />;

  const names = new Map((workflows.data ?? []).map((workflow) => [workflow.id, workflow.name]));
  const rows = runs.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-2 border-b border-line pr-5 pl-8">
        <h1 className="text-[15px] font-semibold">Runs</h1>
        {runs.data ? <span className="text-[13px] text-ink-4">{rows.length}</span> : null}
        <span className="grow" />
        <div className="flex gap-0.5 rounded-lg bg-sidebar p-0.5">
          {FILTERS.map((option, index) => (
            <button
              key={option.label}
              type="button"
              onClick={() => setFilter(index)}
              className={`rounded-md px-2.5 py-1 text-[12.5px] font-medium ${
                filter === index ? "border border-line bg-surface text-ink" : "text-ink-3"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      <div className="flex min-h-0 grow flex-col gap-4 overflow-y-auto px-8 py-6">
        <p className="max-w-[640px] text-[13px] leading-relaxed text-ink-3">
          Every time a workflow ran in this workspace, newest first.
        </p>

        {runs.isPending ? <span className="text-sm text-ink-4">Loading…</span> : null}
        {runs.error ? <ErrorNote error={runs.error} /> : null}

        {runs.data && rows.length === 0 ? (
          <div className="flex flex-col items-start gap-3 rounded-[9px] border border-line bg-canvas px-5 py-6">
            <span className="text-[14px] font-medium">
              {filter === 0 ? "Nothing has run yet" : `No ${FILTERS[filter].label.toLowerCase()} runs`}
            </span>
            {filter === 0 ? (
              <>
                <span className="max-w-[520px] text-[13px] leading-relaxed text-ink-3">
                  Open a published workflow and choose Run.
                </span>
                <Link
                  href="/workflows"
                  className="flex h-9 items-center rounded-control bg-ink px-3.5 text-[13px] font-medium text-white hover:bg-ink-2"
                >
                  Go to workflows
                </Link>
              </>
            ) : null}
          </div>
        ) : null}

        {rows.length > 0 ? (
          <div className="min-w-[760px] overflow-hidden rounded-[9px] border border-line">
            <div className="flex gap-4 border-b border-line bg-canvas px-4 py-2 text-[11px] font-semibold tracking-wide text-ink-4 uppercase">
              <span className="flex-[6]">Workflow</span>
              <span className="flex-[2]">State</span>
              <span className="flex-[3]">Progress</span>
              <span className="flex-[2] text-right">Tokens</span>
              <span className="flex-[2] text-right">Took</span>
              <span className="flex-[3] text-right">Started</span>
            </div>
            {rows.map((run) => (
              <Row key={run.id} run={run} name={names.get(run.workflow_id) ?? "A workflow"} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

const BAR: Partial<Record<RunStatus, string>> = {
  succeeded: "bg-[#10b981]",
  failed: "bg-[#ef4444]",
  timed_out: "bg-[#ef4444]",
  running: "bg-[#2563eb]",
};

function Row({ run, name }: { run: RunSummary; name: string }) {
  const took = run.finished_at ? elapsed(run.started_at ?? run.created_at, run.finished_at, 0) : null;
  const share = run.steps ? (run.steps_done / run.steps) * 100 : 0;
  return (
    <Link
      href={`/runs/${run.id}`}
      className="flex items-center gap-4 border-t border-line-soft px-4 py-3 first:border-t-0 hover:bg-canvas"
    >
      <span className="flex min-w-0 flex-[6] flex-col gap-0.5">
        <span className="truncate text-[13.5px] font-medium">{name}</span>
        {run.error ? (
          <span className="truncate text-[12px] text-danger">{run.error.message}</span>
        ) : null}
      </span>
      <span className="flex-[2]">
        <StatusPill status={run.status} />
      </span>
      <span className="flex flex-[3] flex-col gap-1">
        <span className="text-[11.5px] text-ink-3 tabular-nums">
          {run.steps ? `${run.steps_done} of ${run.steps} steps` : "not started"}
        </span>
        <span className="h-[5px] overflow-hidden rounded-full bg-sunken">
          <span
            className={`block h-full rounded-full ${BAR[run.status] ?? "bg-line-strong"}`}
            style={{ width: `${share}%` }}
          />
        </span>
      </span>
      <span className="flex-[2] text-right text-[12.5px] text-ink-3 tabular-nums">
        {formatTokens(run.input_tokens + run.output_tokens)}
      </span>
      <span className="flex-[2] text-right text-[12.5px] text-ink-3 tabular-nums">
        {took === null ? "—" : formatDuration(took)}
      </span>
      <span className="flex-[3] text-right text-[12.5px] text-ink-4">
        {formatStarted(run.created_at)}
      </span>
    </Link>
  );
}
