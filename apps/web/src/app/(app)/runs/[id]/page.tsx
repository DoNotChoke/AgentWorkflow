"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { RunDialog } from "@/components/run-dialog";
import {
  Account,
  BUTTON_PLAIN,
  BUTTON_PRIMARY,
  Chip,
  Fact,
  Part,
  PlayIcon,
  StatusPill,
} from "@/components/run-parts";
import { ErrorNote, Spinner } from "@/components/ui";
import { WorkflowGraph } from "@/components/workflow-graph";
import { Panel } from "@/components/workflow-outline";
import type { IrNode, RunDetail, RunStep, WorkflowIr } from "@/lib/api";
import { useConnections, useMembers, useTools, useWorkflow } from "@/lib/queries";
import {
  allNodes,
  elapsed,
  formatClock,
  formatDuration,
  formatStarted,
  isFinished,
  parsePath,
  type Progress,
  providerOf,
  readProgress,
  useNow,
  useRun,
  useWorkflowVersion,
  useWorkflowVersions,
} from "@/lib/runs";
import { useSession } from "@/lib/session";
import { useTenant } from "@/lib/tenant";

/** Sau ngần này mà chưa bước nào bắt đầu thì nhiều khả năng không có worker. */
const NO_WORKER_AFTER_MS = 10_000;

/**
 * Một lần chạy: đồ thị của version nó ghim, tô theo trạng thái từng bước.
 *
 * Trang hỏi lại server mỗi giây cho tới khi lần chạy kết thúc. Vòng lặp hiện một
 * lượt mỗi lúc: mặc định là lượt đang chạy, cho tới khi người xem chọn lượt khác.
 */
export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { organization, workspace } = useTenant();
  const workspaceId = workspace?.id ?? null;

  const run = useRun(organization.id, workspaceId, id);
  const workflowId = run.data?.workflow_id ?? null;
  const workflow = useWorkflow(organization.id, workspaceId, workflowId);
  const versions = useWorkflowVersions(organization.id, workspaceId, workflowId);
  const pinned = versions.data?.find((version) => version.id === run.data?.workflow_version_id);
  const version = useWorkflowVersion(organization.id, workspaceId, workflowId, pinned?.number ?? null);
  const latest = versions.data?.[0] ?? null;
  const latestVersion = useWorkflowVersion(
    organization.id,
    workspaceId,
    workflowId,
    latest?.number ?? null,
  );

  const ir = version.data?.ir ?? null;
  const progress = useMemo(() => readProgress(run.data?.nodes ?? [], ir), [run.data, ir]);

  const [selected, setSelected] = useState<string | null>(null);
  const [turns, setTurns] = useState<Record<string, number>>({});
  const [rerun, setRerun] = useState(false);
  const selectTurn = useCallback(
    (loop: string, turn: number) => setTurns((all) => ({ ...all, [loop]: turn })),
    [],
  );

  const live = run.data ? !isFinished(run.data.status) : false;
  const now = useNow(live);

  if (!workspace) return <Spinner label="No workspace in this organisation." />;
  if (run.isPending) return <Spinner label="Loading run…" />;
  if (run.error || !run.data) {
    return (
      <div className="flex h-full flex-col items-start gap-3 px-8 py-6">
        <ErrorNote error={run.error} />
        <Link href="/runs" className="text-[13px] font-medium text-accent">
          Back to runs
        </Link>
      </div>
    );
  }

  const data = run.data;
  const waiting = progress.total - progress.done - progress.running - progress.failed - progress.skipped;
  const settled = progress.done + progress.skipped + progress.failed;
  const started = data.nodes.some((step) => step.started_at !== null);
  const stalled =
    live && !started && now - new Date(data.created_at).getTime() > NO_WORKER_AFTER_MS;
  const failedStep = data.nodes.find(
    (step) => step.status === "failed" && step.node_type !== "loop",
  );

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-2.5 border-b border-line pr-5 pl-8">
        <Link href="/runs" className="text-[13px] text-ink-4 hover:text-ink-2">
          Runs
        </Link>
        <span className="text-line-strong">/</span>
        <span className="min-w-0 truncate text-[13px] text-ink-3">
          {workflow.data?.name ?? "Workflow"}
        </span>
        <span className="text-line-strong">/</span>
        <h1 className="shrink-0 text-[15px] font-semibold">{formatStarted(data.created_at)}</h1>
        {pinned ? <Chip>v{pinned.number}</Chip> : null}
        <StatusPill status={data.status} />
        <span className="grow" />
        <Link href={`/workflows/${data.workflow_id}`} className={BUTTON_PLAIN}>
          Open workflow
        </Link>
        {live ? null : (
          <button
            type="button"
            disabled={!latestVersion.data || !workflow.data}
            onClick={() => setRerun(true)}
            className={BUTTON_PRIMARY}
          >
            <PlayIcon />
            Run again
          </button>
        )}
      </header>

      {data.status === "failed" || data.status === "timed_out" ? (
        <div className="flex shrink-0 items-center gap-3 border-b border-danger-line bg-danger-wash px-8 py-2 text-[12.5px] text-danger">
          <span className="min-w-0 grow">
            <b>{failedStep ? `Stopped at ${describePath(failedStep.node_path)}.` : "The run stopped."}</b>{" "}
            {failedStep?.error?.message ?? data.error?.message}
          </span>
          {failedStep ? (
            <button
              type="button"
              className="shrink-0 font-medium underline"
              onClick={() => {
                const at = parsePath(failedStep.node_path);
                if (at) selectTurn(at.loop, at.turn);
                setSelected(failedStep.node_id);
              }}
            >
              Show the step
            </button>
          ) : null}
        </div>
      ) : null}
      {stalled ? (
        <div className="shrink-0 border-b border-[#f2dfb8] bg-[#fff8ec] px-8 py-2 text-[12.5px] text-warn">
          <b>Nothing has picked this run up yet.</b> The worker that executes workflows may not be
          running. The run starts as soon as one is.
        </div>
      ) : null}

      <div className="flex min-h-0 grow flex-col lg:flex-row">
        <div className="flex min-h-[460px] min-w-0 grow flex-col">
          <div className="flex shrink-0 flex-col gap-1.5 border-b border-line px-5 py-2.5">
            <div className="flex gap-3.5 text-[11.5px] font-medium tabular-nums">
              <span className="text-ok">{progress.done} done</span>
              {progress.running ? <span className="text-[#1d4ed8]">{progress.running} running</span> : null}
              {progress.skipped ? <span className="text-warn">{progress.skipped} skipped</span> : null}
              {progress.failed ? <span className="text-danger">{progress.failed} failed</span> : null}
              {waiting ? (
                <span className="text-ink-4">
                  {waiting} {live ? "waiting" : "never started"}
                </span>
              ) : null}
              <span className="grow" />
              <span className="font-normal text-ink-4">
                {progress.total ? `${settled} of ${progress.total} steps` : "no step yet"}
                {live ? " · refreshes every second" : ""}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-sunken">
              <div
                className={`h-full rounded-full transition-[width] duration-500 ${
                  progress.failed ? "bg-[#ef4444]" : data.status === "succeeded" ? "bg-[#10b981]" : "bg-[#2563eb]"
                }`}
                style={{ width: `${progress.total ? (settled / progress.total) * 100 : 0}%` }}
              />
            </div>
          </div>
          <div className="min-h-0 grow">
            {ir ? (
              <WorkflowGraph
                ir={ir}
                progress={progress}
                shownTurns={turns}
                onSelectTurn={selectTurn}
                selected={selected}
                onSelect={setSelected}
              />
            ) : (
              <Spinner label={version.error ? "Could not load this version." : "Loading steps…"} />
            )}
          </div>
        </div>

        <aside className="flex w-full shrink-0 flex-col gap-3.5 border-t border-line bg-canvas px-5 py-5 lg:w-[372px] lg:overflow-y-auto lg:border-t-0 lg:border-l [&>*]:shrink-0">
          <RunFacts run={data} ir={ir} progress={progress} now={now} />
          {ir && selected ? (
            <StepFacts
              ir={ir}
              nodeId={selected}
              progress={progress}
              turns={turns}
              now={now}
            />
          ) : (
            <span className="px-1 text-[12.5px] text-ink-4">Select a step to see how it went.</span>
          )}
          {ir && !live && data.status !== "succeeded" ? (
            <RepeatWarning ir={ir} steps={data.nodes} />
          ) : null}
        </aside>
      </div>

      {rerun && workflow.data && latest && latestVersion.data ? (
        <RunDialog
          workflow={workflow.data}
          versionNumber={latest.number}
          ir={latestVersion.data.ir}
          initialInputs={data.inputs}
          onClose={() => setRerun(false)}
          onStarted={(runId) => {
            setRerun(false);
            router.push(`/runs/${runId}`);
          }}
        />
      ) : null}
    </div>
  );
}

/** `moi_cau[3].dang` → `turn 4, step dang`. */
function describePath(path: string): string {
  const at = parsePath(path);
  return at ? `turn ${at.turn + 1}, step ${at.node}` : `step ${path}`;
}

function RunFacts({
  run,
  ir,
  progress,
  now,
}: {
  run: RunDetail;
  ir: WorkflowIr | null;
  progress: Progress;
  now: number;
}) {
  const { organization, workspace } = useTenant();
  const { user } = useSession();
  const members = useMembers(organization.id);
  const connections = useConnections(organization.id, workspace?.id ?? null);

  const who =
    run.initiated_by === user?.id
      ? "you"
      : (members.data?.find((member) => member.user_id === run.initiated_by)?.display_name ??
        "someone else");
  const took = elapsed(run.started_at ?? run.created_at, run.finished_at, now);
  const tokens = progress.inputTokens + progress.outputTokens;

  // Mỗi provider một tài khoản; lần chạy lưu theo node nên gom lại để hiển thị.
  const accounts = new Map<string, string>();
  for (const node of ir ? allNodes(ir) : []) {
    const provider = providerOf(node);
    const connectionId = run.connections[node.id];
    if (provider && connectionId) accounts.set(provider, connectionId);
  }

  return (
    <Panel title="This run">
      <Part>
        <Fact name="Status">
          <StatusPill status={run.status} />
        </Fact>
        <Fact name="Started">
          {formatStarted(run.created_at)} · {who}
        </Fact>
        <Fact name={run.finished_at ? "Took" : "Elapsed"}>
          {took === null ? "—" : formatDuration(took)}
        </Fact>
        <Fact name="Model tokens">
          {tokens
            ? `${progress.inputTokens.toLocaleString()} in · ${progress.outputTokens.toLocaleString()} out`
            : "none yet"}
        </Fact>
      </Part>
      {Object.keys(run.inputs).length ? (
        <Part>
          {Object.entries(run.inputs).map(([name, value]) => (
            <Fact key={name} name={name} mono>
              {typeof value === "string" ? value : JSON.stringify(value)}
            </Fact>
          ))}
        </Part>
      ) : null}
      {accounts.size ? (
        <Part>
          {[...accounts].map(([provider, connectionId]) => {
            const connection = connections.data?.find((item) => item.id === connectionId);
            return (
              <Account
                key={provider}
                provider={provider}
                name={connection?.display_name ?? "An account you cannot see"}
                note={connection ? (connection.sharing === "private" ? "private" : "shared") : undefined}
              />
            );
          })}
        </Part>
      ) : null}
    </Panel>
  );
}

function StepFacts({
  ir,
  nodeId,
  progress,
  turns,
  now,
}: {
  ir: WorkflowIr;
  nodeId: string;
  progress: Progress;
  turns: Record<string, number>;
  now: number;
}) {
  const loop = ir.nodes.find((node) => node.body?.some((inner) => inner.id === nodeId)) ?? null;
  const node: IrNode | undefined = loop
    ? loop.body?.find((inner) => inner.id === nodeId)
    : ir.nodes.find((top) => top.id === nodeId);
  if (!node) return null;

  const loopProgress = loop ? progress.loops.get(loop.id) : undefined;
  const turn = loop ? (turns[loop.id] ?? loopProgress?.current ?? 0) : null;
  const path = loop ? `${loop.id}[${turn}].${node.id}` : node.id;
  const step = progress.byPath.get(path);
  const took = step ? elapsed(step.started_at, step.finished_at, now) : null;
  const own = node.type === "loop" ? progress.loops.get(node.id) : undefined;

  return (
    <Panel
      title="Selected step"
      tone={step?.status === "failed" ? "error" : "plain"}
      count={node.type}
    >
      <Part>
        <span className="font-mono text-[13px] font-medium break-all">{path}</span>
        <Fact name="Status">
          {step ? <StatusPill status={step.status} /> : <span className="text-ink-4">not reached</span>}
        </Fact>
        {node.tool_id ? <Fact name="Uses" mono>{node.tool_id}</Fact> : null}
        {node.model ? <Fact name="Model" mono>{node.model}</Fact> : null}
      </Part>

      {step?.error ? (
        <Part>
          <span className="font-mono text-[11.5px] font-medium text-danger">{step.error.category}</span>
          <span className="text-[12.5px] leading-relaxed break-words text-danger">
            {step.error.message}
          </span>
        </Part>
      ) : null}

      <Part>
        {loop && turn !== null ? (
          <Fact name="Turn">
            {turn + 1} of {loopProgress?.turns.length ?? "?"}
          </Fact>
        ) : null}
        {own ? (
          <Fact name="Turns">
            {own.turns.filter((status) => status === "succeeded").length} of {own.turns.length} done
          </Fact>
        ) : null}
        <Fact name="Started">{step?.started_at ? formatClock(step.started_at) : "—"}</Fact>
        <Fact name="Took">{took === null ? "—" : formatDuration(took)}</Fact>
        <Fact name="Attempts">{step?.attempt ? String(step.attempt) : "—"}</Fact>
        {step?.usage ? (
          <Fact name="Model tokens">
            {(step.usage.input_tokens ?? 0).toLocaleString()} in ·{" "}
            {(step.usage.output_tokens ?? 0).toLocaleString()} out
          </Fact>
        ) : null}
      </Part>

      {node.type === "tool" || node.type === "llm" || node.type === "transform" ? (
        <Part>
          <span className="text-[11px] font-semibold tracking-wide text-ink-4 uppercase">
            Input and output
          </span>
          <span className="rounded-control border border-dashed border-line-strong px-3 py-2 text-[12px] leading-relaxed text-ink-4">
            What this step received and produced is not recorded yet.
          </span>
        </Part>
      ) : null}
    </Panel>
  );
}

/** Chạy lại là một lần chạy mới: những bước ghi ra ngoài đã xong sẽ ghi lần nữa. */
function RepeatWarning({ ir, steps }: { ir: WorkflowIr; steps: RunStep[] }) {
  const tools = useTools();
  const writing = new Set(
    allNodes(ir)
      .filter(
        (node) =>
          node.type === "tool" &&
          tools.data?.find((tool) => tool.tool_id === node.tool_id)?.side_effect === "write",
      )
      .map((node) => node.id),
  );
  const written = steps.filter(
    (step) => step.status === "succeeded" && writing.has(step.node_id),
  ).length;
  if (written === 0) return null;
  return (
    <Panel title="Before you run again" tone="warn">
      <Part>
        <span className="text-[12.5px] leading-relaxed text-ink-2">
          A new run starts from the first step. {written}{" "}
          {written === 1 ? "step that writes outside has" : "steps that write outside have"} already
          finished in this run; running again makes those writes a second time.
        </span>
      </Part>
    </Panel>
  );
}
