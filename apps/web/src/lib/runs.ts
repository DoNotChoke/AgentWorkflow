"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  apiRequest,
  type IrNode,
  type Run,
  type RunDetail,
  type RunStatus,
  type RunStep,
  type RunSummary,
  type StepStatus,
  type WorkflowIr,
  type WorkflowVersion,
  type WorkflowVersionSummary,
} from "@/lib/api";
import { queryKeys, workspaceHeaders } from "@/lib/queries";
import { useSession } from "@/lib/session";

/** Lần chạy của workflow: gọi API, hỏi lại định kỳ, và đọc tiến trình theo lượt. */

const POLL_MS = 1000;
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

/** Một lần chạy kèm từng bước; hỏi lại mỗi giây cho tới khi nó kết thúc. */
export function useRun(
  organizationId: string | null,
  workspaceId: string | null,
  runId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.run(organizationId, runId),
    enabled: status === "authenticated" && workspaceId !== null && runId !== null,
    refetchInterval: (state) =>
      state.state.data && isFinished(state.state.data.status) ? false : POLL_MS,
    queryFn: () =>
      apiRequest<RunDetail>(`/v1/executions/${runId}`, {
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
