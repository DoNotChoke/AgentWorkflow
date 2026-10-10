"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  apiRequest,
  type IrNode,
  type PayloadSide,
  type Run,
  type RunDetail,
  type RunStatus,
  type RunStep,
  type RunSummary,
  type RunTrace,
  type StepStatus,
  type TracePayload,
  type TraceStep,
  type WorkflowIr,
  type WorkflowVersion,
  type WorkflowVersionSummary,
} from "@/lib/api";
import { queryKeys, workspaceHeaders } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { streamSse } from "@/lib/sse";

/** Lần chạy của workflow: gọi API, theo dõi qua SSE, và đọc tiến trình theo lượt. */

const POLL_MS = 1000;
/** Chờ lâu nhất giữa hai lần nối lại luồng theo dõi. */
const RECONNECT_MAX_MS = 15_000;
const LIST_POLL_MS = 3000;

const FINISHED: ReadonlySet<RunStatus> = new Set(["succeeded", "failed", "cancelled", "timed_out"]);

export function isFinished(status: RunStatus): boolean {
  return FINISHED.has(status);
}

// ─── Version ────────────────────────────────────────────────────────────────

export function useWorkflowVersions(
  organizationId: string | null,
  workspaceId: string | null,
  workflowId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.workflowVersions(organizationId, workflowId),
    enabled: status === "authenticated" && workspaceId !== null && workflowId !== null,
    queryFn: () =>
      apiRequest<WorkflowVersionSummary[]>(`/v1/workflows/${workflowId}/versions`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export function useWorkflowVersion(
  organizationId: string | null,
  workspaceId: string | null,
  workflowId: string | null,
  number: number | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.workflowVersion(organizationId, workflowId, number),
    enabled:
      status === "authenticated" && workspaceId !== null && workflowId !== null && number !== null,
    // Một version đã publish không bao giờ đổi.
    staleTime: Infinity,
    queryFn: () =>
      apiRequest<WorkflowVersion>(`/v1/workflows/${workflowId}/versions/${number}`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export function usePublishWorkflow(organizationId: string | null, workspaceId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) =>
      apiRequest<WorkflowVersion>(`/v1/workflows/${workflowId}/publish`, {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
    onSuccess: (_version, workflowId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.workflow(organizationId, workflowId) });
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowVersions(organizationId, workflowId),
      });
      queryClient.invalidateQueries({ queryKey: queryKeys.workflows(organizationId, workspaceId) });
    },
  });
}

// ─── Lần chạy ───────────────────────────────────────────────────────────────

export type RunFilter = { workflowId?: string; status?: RunStatus; limit?: number };

/** Hỏi lại mỗi vài giây chừng nào trong danh sách còn lần chạy chưa kết thúc. */
export function useRuns(
  organizationId: string | null,
  workspaceId: string | null,
  filter: RunFilter = {},
) {
  const { accessToken, status } = useSession();
  const query = new URLSearchParams();
  if (filter.workflowId) query.set("workflow_id", filter.workflowId);
  if (filter.status) query.set("status", filter.status);
  if (filter.limit) query.set("limit", String(filter.limit));
  const search = query.toString();
  return useQuery({
    queryKey: [...queryKeys.runs(organizationId, workspaceId), search],
    enabled: status === "authenticated" && organizationId !== null && workspaceId !== null,
    refetchInterval: (state) =>
      state.state.data?.some((run) => !isFinished(run.status)) ? LIST_POLL_MS : false,
    queryFn: () =>
      apiRequest<RunSummary[]>(`/v1/executions${search ? `?${search}` : ""}`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

/**
 * Một lần chạy kèm từng bước. `poll` thì hỏi lại mỗi giây cho tới khi nó kết thúc;
 * không thì có thứ khác đang ghi vào cache (`useLiveRun`).
 */
export function useRun(
  organizationId: string | null,
  workspaceId: string | null,
  runId: string | null,
  poll = true,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.run(organizationId, runId),
    enabled: status === "authenticated" && workspaceId !== null && runId !== null,
    refetchInterval: (state) =>
      !poll || (state.state.data && isFinished(state.state.data.status)) ? false : POLL_MS,
    queryFn: () =>
      apiRequest<RunDetail>(`/v1/executions/${runId}`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

/**
 * Một lần chạy, theo dõi qua SSE: server gửi cả lần chạy một lần rồi chỉ gửi thứ đã
 * đổi, và hook ghi chúng vào cache của `useRun`. Luồng đứt thì nối lại, chờ lâu dần;
 * trong lúc chưa nối được thì `useRun` hỏi lại mỗi giây như trước.
 */
export function useLiveRun(
  organizationId: string | null,
  workspaceId: string | null,
  runId: string | null,
) {
  const { accessToken, status } = useSession();
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const run = useRun(organizationId, workspaceId, runId, !connected);
  const finished = run.data ? isFinished(run.data.status) : false;

  useEffect(() => {
    if (status !== "authenticated" || workspaceId === null || runId === null || finished) return;
    const abort = new AbortController();
    const key = queryKeys.run(organizationId, runId);
    let retry: number | undefined;
    let failures = 0;

    const follow = async () => {
      let ended = false;
      try {
        const events = streamSse(`/v1/executions/${runId}/events`, {
          method: "GET",
          accessToken,
          headers: workspaceHeaders(organizationId, workspaceId),
          signal: abort.signal,
        });
        for await (const { event, data } of events) {
          failures = 0;
          setConnected(true);
          if (event === "snapshot") {
            queryClient.setQueryData<RunDetail>(key, data as RunDetail);
          } else if (event === "execution") {
            queryClient.setQueryData<RunDetail>(key, (seen) =>
              seen ? { ...seen, ...(data as Run) } : seen,
            );
          } else if (event === "nodes") {
            const changed = (data as { nodes: RunStep[] }).nodes;
            queryClient.setQueryData<RunDetail>(key, (seen) => {
              if (!seen) return seen;
              const byPath = new Map(seen.nodes.map((step) => [step.node_path, step]));
              for (const step of changed) byPath.set(step.node_path, step);
              return { ...seen, nodes: [...byPath.values()] };
            });
            // Một bước vừa xong thì trace có thêm bước của nó.
            if (changed.some((step) => step.status !== "pending" && step.status !== "running")) {
              queryClient.invalidateQueries({
                queryKey: queryKeys.runTrace(organizationId, runId),
              });
            }
          } else if (event === "done") {
            ended = true;
          }
        }
      } catch {
        // Luồng đứt hoặc không mở được: nối lại bên dưới.
      }
      if (abort.signal.aborted) return;
      setConnected(false);
      if (ended) return;
      retry = window.setTimeout(follow, Math.min(1000 * 2 ** failures, RECONNECT_MAX_MS));
      failures += 1;
    };
    void follow();

    return () => {
      abort.abort();
      window.clearTimeout(retry);
    };
  }, [accessToken, finished, organizationId, queryClient, runId, status, workspaceId]);

  return { run, connected: connected && !finished };
}

/**
 * Execution Trace của một lần chạy: mỗi lần thử của một bước là một bước trace.
 * `null` khi lần chạy không để lại trace. `useLiveRun` làm nó tải lại mỗi khi một bước
 * xong; `poll` là đường lùi khi luồng theo dõi chưa nối được. Tải thêm một lần khi lần
 * chạy kết thúc: bước cuối được ghi trước khi lần chạy đổi trạng thái.
 */
export function useRunTrace(
  organizationId: string | null,
  workspaceId: string | null,
  runId: string | null,
  live: boolean,
  poll: boolean,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: [...queryKeys.runTrace(organizationId, runId), live],
    enabled: status === "authenticated" && workspaceId !== null && runId !== null,
    placeholderData: keepPreviousData,
    refetchInterval: live && poll ? POLL_MS : false,
    queryFn: () =>
      apiRequest<RunTrace | null>(`/v1/executions/${runId}/trace`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

/** Các lần thử của từng bước, theo `node_path`, lần cũ trước. Bước của Gateway nằm dưới
 * bước của node và không có `node_path` riêng. */
export function attemptsByPath(trace: RunTrace | null | undefined): Map<string, TraceStep[]> {
  const found = new Map<string, TraceStep[]>();
  for (const step of trace?.steps ?? []) {
    const path = step.attributes.node_path;
    if (typeof path !== "string") continue;
    found.set(path, [...(found.get(path) ?? []), step]);
  }
  return found;
}

/** Nội dung một phía payload của một bước trace. Bước đã ghi thì không đổi. */
export function useTracePayload(
  organizationId: string | null,
  workspaceId: string | null,
  traceId: string,
  stepId: string,
  side: PayloadSide,
  enabled: boolean,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.tracePayload(organizationId, traceId, stepId, side),
    enabled: enabled && status === "authenticated" && workspaceId !== null,
    staleTime: Infinity,
    queryFn: () =>
      apiRequest<TracePayload>(`/v1/traces/${traceId}/steps/${stepId}/payload/${side}`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export type StartRun = {
  workflowId: string;
  inputs: Record<string, unknown>;
  connectionsByProvider: Record<string, string>;
};

export function useStartRun(organizationId: string | null, workspaceId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (start: StartRun) =>
      apiRequest<Run>("/v1/executions", {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
        body: {
          workflow_id: start.workflowId,
          inputs: start.inputs,
          connections_by_provider: start.connectionsByProvider,
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.runs(organizationId, workspaceId) });
    },
  });
}

// ─── Đọc tiến trình ─────────────────────────────────────────────────────────

/** Mọi node của một IR, kể cả trong thân vòng lặp. */
export function allNodes(ir: WorkflowIr): IrNode[] {
  return ir.nodes.flatMap((node) => [node, ...(node.body ?? [])]);
}

/** `notion.search` → `notion`. Node không phải tool thì `null`. */
export function providerOf(node: IrNode): string | null {
  if (node.type !== "tool" || !node.tool_id) return null;
  const dot = node.tool_id.indexOf(".");
  return dot > 0 ? node.tool_id.slice(0, dot) : null;
}

const TURN = /^(.+)\[(\d+)\]\.(.+)$/;

/** `moi_cau[2].dang` → vòng lặp `moi_cau`, lượt 2, node `dang`. */
export function parsePath(path: string): { loop: string; turn: number; node: string } | null {
  const found = TURN.exec(path);
  return found ? { loop: found[1], turn: Number(found[2]), node: found[3] } : null;
}

export type LoopProgress = {
  /** Trạng thái từng lượt, theo thứ tự. */
  turns: StepStatus[];
  /** Lượt đang chạy; không có thì lượt hỏng; không có thì lượt cuối. */
  current: number;
};

export type Progress = {
  byPath: Map<string, RunStep>;
  loops: Map<string, LoopProgress>;
  total: number;
  done: number;
  running: number;
  failed: number;
  skipped: number;
  inputTokens: number;
  outputTokens: number;
};

export function readProgress(steps: RunStep[], ir: WorkflowIr | null): Progress {
  const byPath = new Map(steps.map((step) => [step.node_path, step]));
  const count = (status: StepStatus) => steps.filter((step) => step.status === status).length;

  const perTurn = new Map<string, StepStatus[][]>();
  for (const step of steps) {
    const at = parsePath(step.node_path);
    if (!at) continue;
    const turns = perTurn.get(at.loop) ?? [];
    (turns[at.turn] ??= []).push(step.status);
    perTurn.set(at.loop, turns);
  }

  const loops = new Map<string, LoopProgress>();
  for (const [loop, raw] of perTurn) {
    const size = ir?.nodes.find((node) => node.id === loop)?.body?.length ?? 0;
    const turns = Array.from(raw, (statuses = []) => turnStatus(statuses, size));
    const running = turns.indexOf("running");
    const failed = turns.indexOf("failed");
    const lastDone = turns.lastIndexOf("succeeded");
    const current = running >= 0 ? running : failed >= 0 ? failed : Math.max(lastDone, 0);
    loops.set(loop, { turns, current });
  }

  return {
    byPath,
    loops,
    total: steps.length,
    done: count("succeeded"),
    running: count("running"),
    failed: count("failed"),
    skipped: count("skipped"),
    inputTokens: steps.reduce((sum, step) => sum + (step.usage?.input_tokens ?? 0), 0),
    outputTokens: steps.reduce((sum, step) => sum + (step.usage?.output_tokens ?? 0), 0),
  };
}

function turnStatus(statuses: StepStatus[], size: number): StepStatus {
  if (statuses.includes("failed")) return "failed";
  if (statuses.includes("running")) return "running";
  const settled = statuses.filter((status) => status === "succeeded" || status === "skipped");
  if (settled.length > 0 && settled.length >= Math.max(size, statuses.length)) return "succeeded";
  // Giữa hai bước của cùng một lượt không có hàng nào `running`.
  return settled.length > 0 ? "running" : "pending";
}

// ─── Định dạng ──────────────────────────────────────────────────────────────

/** Đồng hồ nhảy mỗi giây chừng nào `active`. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export function formatDuration(ms: number): string {
  if (ms < 0) return "0s";
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatTokens(count: number): string {
  if (count === 0) return "—";
  return count < 1000 ? String(count) : `${(count / 1000).toFixed(1)}K`;
}

export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatStarted(iso: string): string {
  const then = new Date(iso);
  const time = then.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (then.toDateString() === new Date().toDateString()) return `Today ${time}`;
  return `${then.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${time}`;
}

/** Thời lượng của một lần chạy hoặc một bước; đang chạy thì tính tới `now`. */
export function elapsed(
  from: string | null,
  to: string | null,
  now: number,
): number | null {
  if (!from) return null;
  return (to ? new Date(to).getTime() : now) - new Date(from).getTime();
}
