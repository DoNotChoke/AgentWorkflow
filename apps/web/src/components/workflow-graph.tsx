"use client";

import {
  Background,
  BackgroundVariant,
  type BuiltInEdge,
  Controls,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useRef } from "react";
import { hasProviderLogo, ProviderIcon } from "@/components/provider-icon";
import type { IrNode, StepStatus, WorkflowIr } from "@/lib/api";
import { type Progress, providerOf } from "@/lib/runs";

/**
 * Một Workflow IR vẽ thành đồ thị, chỉ đọc.
 *
 * Icon nói node thuộc loại nào; viền và nền của thẻ nói lần chạy đang ở đâu.
 * Không có `progress` thì mọi thẻ ở trạng thái nghỉ — đó là trang workflow.
 *
 * Workflow chạy tuần tự và vòng lặp không lồng nhau, nên bố cục tự tính: các
 * node xếp dọc, thân vòng lặp nằm trong một khung và gập thành hai cột khi dài.
 */

const W = 236;
const H = 58;
const GAP = 26;
const PAD = 24;
const HEAD = 52;
const COLUMN_GAP = 36;
const MAX_CHIPS = 10;

type StepData = { node: IrNode; status: StepStatus | null; selected: boolean };
type LoopData = StepData & {
  width: number;
  height: number;
  turns: StepStatus[] | null;
  shown: number;
  onTurn: (turn: number) => void;
};

type StepNodeType = Node<StepData, "step">;
type LoopNodeType = Node<LoopData, "loop">;

export function WorkflowGraph({
  ir,
  progress,
  shownTurns,
  onSelectTurn,
  selected,
  onSelect,
}: {
  ir: WorkflowIr;
  progress?: Progress;
  /** Lượt đang xem của từng vòng lặp. */
  shownTurns?: Record<string, number>;
  onSelectTurn?: (loop: string, turn: number) => void;
  selected: string | null;
  onSelect: (nodeId: string | null) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const nodes: (StepNodeType | LoopNodeType)[] = [];
    const edges: BuiltInEdge[] = [];
    const statusAt = (path: string) => progress?.byPath.get(path)?.status ?? null;

    let y = 0;
    let previous: {
      id: string;
      status: StepStatus | null;
      type: IrNode["type"];
    } | null = null;

    for (const node of ir.nodes) {
      const status = statusAt(node.id);
      if (previous) edges.push(edge(previous.id, node.id, previous.status, status, previous.type));

      if (node.type !== "loop") {
        nodes.push({
          id: node.id,
          type: "step",
          position: { x: -W / 2, y },
          width: W,
          height: H,
          data: { node, status, selected: selected === node.id },
        });
        y += H + GAP;
      } else {
        const body = node.body ?? [];
        const columns = body.length > 3 ? 2 : 1;
        const rows = Math.max(1, Math.ceil(body.length / columns));
        const width = PAD * 2 + columns * W + (columns - 1) * COLUMN_GAP;
        const height = HEAD + rows * H + (rows - 1) * GAP + PAD;
        const loop = progress?.loops.get(node.id);
        const shown = shownTurns?.[node.id] ?? loop?.current ?? 0;

        nodes.push({
          id: node.id,
          type: "loop",
          position: { x: -width / 2, y },
          width,
          height,
          data: {
            node,
            status,
            selected: selected === node.id,
            width,
            height,
            turns: loop?.turns ?? null,
            shown,
            onTurn: (turn) => onSelectTurn?.(node.id, turn),
          },
        });

        let before: {
          id: string;
          status: StepStatus | null;
          type: IrNode["type"];
        } | null = null;
        body.forEach((inner, index) => {
          const innerStatus = statusAt(`${node.id}[${shown}].${inner.id}`);
          const id = `${node.id}/${inner.id}`;
          nodes.push({
            id,
            type: "step",
            parentId: node.id,
            extent: "parent",
            position: {
              x: PAD + Math.floor(index / rows) * (W + COLUMN_GAP),
              y: HEAD + (index % rows) * (H + GAP),
            },
            width: W,
            height: H,
            data: {
              node: inner,
              status: innerStatus,
              selected: selected === inner.id,
            },
          });
          if (before) edges.push(edge(before.id, id, before.status, innerStatus, before.type));
          before = { id, status: innerStatus, type: inner.type };
        });
        y += height + GAP;
      }
      previous = { id: node.id, status, type: node.type };
    }
    return { nodes, edges };
  }, [ir, progress, shownTurns, onSelectTurn, selected]);

  // Thu gọn sidebar hay đổi cỡ cửa sổ làm khung đổi rộng; đồ thị phải nằm lại giữa khung.
  const frame = useRef<HTMLDivElement>(null);
  const flow = useRef<ReactFlowInstance<StepNodeType | LoopNodeType, BuiltInEdge> | null>(null);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      void flow.current?.fitView(FIT);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={frame} className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onInit={(instance) => {
          flow.current = instance;
        }}
        fitView
        fitViewOptions={FIT}
        minZoom={0.35}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        onNodeClick={(_event, node) => onSelect(node.data.node.id)}
        onPaneClick={() => onSelect(null)}
        className="bg-canvas"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.4} color="#dcdcd7" />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
    </div>
  );
}

const FIT = { padding: 0.12, maxZoom: 1 };

const SETTLED: ReadonlySet<StepStatus | null> = new Set(["succeeded", "skipped", "failed"]);

function edge(
  source: string,
  target: string,
  from: StepStatus | null,
  to: StepStatus | null,
  sourceType: IrNode["type"],
): BuiltInEdge {
  const ahead = to === "running";
  const behind = (from === "succeeded" || from === "skipped") && SETTLED.has(to);
  return {
    id: `${source}->${target}`,
    source,
    target,
    type: "smoothstep",
    pathOptions: { offset: 10, borderRadius: 8 },
    animated: ahead,
    label: sourceType === "condition" ? "if true" : undefined,
    labelStyle: { fontSize: 11, fill: "#6b6b66" },
    style: {
      stroke: ahead ? "#2563eb" : behind ? "#10b981" : "#d0d0cb",
      strokeWidth: ahead ? 2.5 : 2,
    },
  };
}

// ─── Thẻ node ───────────────────────────────────────────────────────────────

const CARD: Record<StepStatus | "idle", string> = {
  idle: "border-line-strong bg-surface",
  pending: "border-line-strong bg-surface",
  running: "border-[#60a5fa] bg-[#eff6ff] shadow-[0_0_0_3px_#bfdbfe]",
  succeeded: "border-[#34d399] bg-[#ecfdf5]",
  failed: "border-[#f87171] bg-[#fef2f2]",
  skipped: "border-[#fbbf24] bg-[#fffbeb]",
};

const HANDLE = "!size-2 !border-[1.5px] !border-line-strong !bg-surface";

function StepNode({ data }: NodeProps<StepNodeType>) {
  const { node, status, selected } = data;
  return (
    <div
      className={`relative flex h-full w-full cursor-pointer items-center gap-2.5 rounded-xl border-2 px-3 ${
        CARD[status ?? "idle"]
      } ${selected ? "outline-2 outline-offset-2 outline-ink" : ""}`}
    >
      <Handle type="target" position={Position.Top} className={HANDLE} isConnectable={false} />
      <KindIcon node={node} />
      <div className="min-w-0">
        <div className="truncate text-[12.5px] font-semibold text-ink-2">{node.id}</div>
        <div className="truncate font-mono text-[10.5px] text-ink-4">{uses(node)}</div>
      </div>
      <Mark status={status} />
      <Handle type="source" position={Position.Bottom} className={HANDLE} isConnectable={false} />
    </div>
  );
}

function Mark({ status }: { status: StepStatus | null }) {
  if (status !== "succeeded" && status !== "failed") return null;
  return (
    <span
      className={`absolute -top-2 -right-2 flex size-4 items-center justify-center rounded-full ${
        status === "succeeded" ? "bg-[#10b981]" : "bg-[#ef4444]"
      }`}
    >
      <svg
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#fff"
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        {status === "succeeded" ? <path d="M20 6 9 17l-5-5" /> : <path d="M18 6 6 18M6 6l12 12" />}
      </svg>
    </span>
  );
}

const FRAME: Record<StepStatus | "idle", string> = {
  idle: "border-[#f3a8cf] bg-[#fdf2f8]/60",
  pending: "border-[#f3a8cf] bg-[#fdf2f8]/60",
  running: "border-[#60a5fa] bg-[#eff6ff]/70",
  succeeded: "border-[#34d399] bg-[#ecfdf5]/60",
  failed: "border-[#f87171] bg-[#fef2f2]/70",
  skipped: "border-[#fbbf24] bg-[#fffbeb]/70",
};

function LoopNode({ data }: NodeProps<LoopNodeType>) {
  const { node, status, selected, turns, shown, onTurn } = data;
  const done = turns?.filter((turn) => turn === "succeeded").length ?? 0;
  return (
    <div
      className={`h-full w-full rounded-2xl border-2 border-dashed ${FRAME[status ?? "idle"]} ${
        selected ? "outline-2 outline-offset-2 outline-ink" : ""
      }`}
    >
      <Handle type="target" position={Position.Top} className={HANDLE} isConnectable={false} />
      <div className="flex items-center gap-2 px-3" style={{ height: HEAD }}>
        <KindIcon node={node} small />
        <span className="truncate text-[12.5px] font-semibold text-ink-2">{node.id}</span>
        <span className="grow" />
        {turns === null ? (
          <span className="shrink-0 text-[11px] text-ink-3">once per item · in order</span>
        ) : turns.length <= MAX_CHIPS ? (
          <>
            <span className="shrink-0 text-[11px] text-ink-3 tabular-nums">
              {done} of {turns.length} turns done
            </span>
            <div className="nodrag nopan flex shrink-0 gap-1.5">
              {turns.map((turn, index) => (
                <button
                  key={index}
                  type="button"
                  title={`Turn ${index + 1}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onTurn(index);
                  }}
                  className={`flex size-[22px] items-center justify-center rounded-md border text-[11px] font-semibold ${
                    TURN[turn]
                  } ${index === shown ? "outline-2 outline-offset-1 outline-ink" : ""}`}
                >
                  {index + 1}
                </button>
              ))}
            </div>
          </>
        ) : (
          <div className="nodrag nopan flex shrink-0 items-center gap-1.5 text-[11.5px] text-ink-2 tabular-nums">
            <span className="text-ink-3">{done} done ·</span>
            <TurnStep
              label="Previous turn"
              disabled={shown === 0}
              onClick={() => onTurn(shown - 1)}
            >
              ‹
            </TurnStep>
            <span>
              turn {shown + 1} of {turns.length}
            </span>
            <TurnStep
              label="Next turn"
              disabled={shown >= turns.length - 1}
              onClick={() => onTurn(shown + 1)}
            >
              ›
            </TurnStep>
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className={HANDLE} isConnectable={false} />
    </div>
  );
}

const TURN: Record<StepStatus, string> = {
  pending: "border-line bg-surface text-ink-4",
  running: "border-[#2563eb] bg-[#2563eb] text-white",
  succeeded: "border-[#d1fae5] bg-[#d1fae5] text-[#047857]",
  failed: "border-[#ef4444] bg-[#ef4444] text-white",
  skipped: "border-[#fde68a] bg-[#fef3c7] text-warn",
};

function TurnStep({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      className="flex size-[22px] items-center justify-center rounded-md border border-line bg-surface text-[13px] disabled:opacity-40"
    >
      {children}
    </button>
  );
}

const NODE_TYPES = { step: StepNode, loop: LoopNode };

// ─── Icon theo loại node ────────────────────────────────────────────────────

function uses(node: IrNode): string {
  if (node.type === "tool") return node.tool_id ?? "tool";
  if (node.type === "llm") return node.model ? `model · ${node.model}` : "model";
  if (node.type === "trigger") {
    const inputs = Object.keys(node.input_schema?.properties ?? {}).length;
    return inputs ? `manual · ${inputs} ${inputs === 1 ? "input" : "inputs"}` : "manual";
  }
  if (node.type === "transform") return "code";
  if (node.type === "condition") return node.op ? `if ${node.op}` : "condition";
  return "each item · in order";
}

const TILE: Record<IrNode["type"], { tile: string; ink: string }> = {
  trigger: { tile: "bg-[#d1fae5]", ink: "#059669" },
  tool: { tile: "bg-[#e0f2fe]", ink: "#0284c7" },
  llm: { tile: "bg-[#ede9fe]", ink: "#7c3aed" },
  loop: { tile: "bg-[#fce7f3]", ink: "#db2777" },
  transform: { tile: "bg-[#ccfbf1]", ink: "#0d9488" },
  condition: { tile: "bg-[#fef3c7]", ink: "#d97706" },
};

export function KindIcon({ node, small = false }: { node: IrNode; small?: boolean }) {
  const box = small ? "size-6 rounded-[7px]" : "size-7 rounded-lg";
  const provider = providerOf(node);
  if (provider && hasProviderLogo(provider)) {
    return (
      <span
        className={`flex shrink-0 items-center justify-center border border-line bg-surface ${box}`}
      >
        <ProviderIcon provider={provider} size={small ? 14 : 17} />
      </span>
    );
  }
  const look = TILE[node.type];
  return (
    <span className={`flex shrink-0 items-center justify-center ${look.tile} ${box}`}>
      <svg
        width={small ? 13 : 16}
        height={small ? 13 : 16}
        viewBox="0 0 24 24"
        fill="none"
        stroke={look.ink}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        {GLYPH[node.type]}
      </svg>
    </span>
  );
}

const GLYPH: Record<IrNode["type"], React.ReactNode> = {
  trigger: (
    <>
      <circle cx="12" cy="12" r="10" />
      <polygon points="10 8 16 12 10 16 10 8" />
    </>
  ),
  tool: (
    <>
      <path d="M12 22v-5" />
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
    </>
  ),
  llm: (
    <>
      <path d="M12 18V5" />
      <path d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4" />
      <path d="M17.598 6.5A3 3 0 1 0 12 5a3 3 0 1 0-5.598 1.5" />
      <path d="M17.997 5.125a4 4 0 0 1 2.526 5.77" />
      <path d="M18 18a4 4 0 0 0 2-7.464" />
      <path d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517" />
      <path d="M6 18a4 4 0 0 1-2-7.464" />
      <path d="M6.003 5.125a4 4 0 0 0-2.526 5.77" />
    </>
  ),
  loop: (
    <>
      <path d="m17 2 4 4-4 4" />
      <path d="M3 11v-1a4 4 0 0 1 4-4h14" />
      <path d="m7 22-4-4 4-4" />
      <path d="M21 13v1a4 4 0 0 1-4 4H3" />
    </>
  ),
  transform: (
    <>
      <path d="M4 7h6" />
      <path d="M7 4v6" />
      <path d="M14 7h6" />
      <path d="m4.5 14.5 5 5" />
      <path d="m9.5 14.5-5 5" />
      <path d="M14 17h6" />
      <path d="M17 14.1v.01" />
      <path d="M17 19.9v.01" />
    </>
  ),
  condition: (
    <>
      <path d="M16 3h5v5" />
      <path d="M8 3H3v5" />
      <path d="M12 22v-8.3a4 4 0 0 0-1.172-2.872L3 3" />
      <path d="m15 9 6-6" />
    </>
  ),
};
