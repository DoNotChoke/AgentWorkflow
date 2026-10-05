import type { ReactNode } from "react";
import type { IrBinding, IrNode, WorkflowIr } from "@/lib/api";

/**
 * Các bước của một Workflow IR, theo thứ tự chạy; thân vòng lặp lồng vào trong.
 *
 * Dùng ở hai chỗ: thẻ giả định trong cửa sổ biên dịch và trang xem draft. Chỉ
 * đọc — không có gì ở đây sửa IR.
 */

const KIND: Record<IrNode["type"], { label: string; look: string }> = {
  trigger: { label: "START", look: "bg-ok-wash text-ok" },
  tool: { label: "TOOL", look: "bg-[#eef1f5] text-[#4a5568]" },
  llm: { label: "LLM", look: "bg-accent-wash text-accent" },
  loop: { label: "LOOP", look: "bg-[#fff3e0] text-warn" },
  transform: { label: "CODE", look: "bg-sunken text-ink-2" },
  condition: { label: "IF", look: "bg-[#fff3e0] text-warn" },
};

/** `{"from_node": …}` → một câu đọc được. */
export function describeBinding(binding: IrBinding | undefined): string {
  if (!binding) return "";
  if ("const" in binding) return shorten(JSON.stringify(binding.const));
  if ("from_input" in binding) return `input ${binding.from_input}`;
  if ("from_item" in binding) {
    return binding.from_item.length ? `current item → ${binding.from_item.join(".")}` : "current item";
  }
  const source = binding.from_node;
  const path = source.path.join(".");
  if (!source.where) return path ? `${source.node} → ${path}` : source.node;
  const keys = Object.entries(source.where)
    .map(([name, value]) => `${name} = ${describeBinding(value)}`)
    .join(", ");
  const pick = source.pick?.length ? ` → ${source.pick.join(".")}` : "";
  return `${source.node} → ${path} where ${keys}${pick}`;
}

function shorten(text: string): string {
  return text.length > 48 ? `${text.slice(0, 48)}…` : text;
}

function summary(node: IrNode): string {
  if (node.type === "trigger") {
    const inputs = Object.keys(node.input_schema?.properties ?? {});
    return inputs.length ? `input: ${inputs.join(", ")}` : "no input";
  }
  if (node.type === "loop") return `once per item of ${describeBinding(node.over)}`;
  if (node.type === "condition") {
    return `${describeBinding(node.left)} ${node.op ?? ""} ${describeBinding(node.right)}`.trim();
  }
  const bindings = node.type === "transform" ? node.args : node.input;
  return Object.entries(bindings ?? {})
    .map(([name, binding]) => `${name} ← ${describeBinding(binding)}`)
    .join(" · ");
}

function Row({ node, depth }: { node: IrNode; depth: number }) {
  const kind = KIND[node.type];
  const what = node.tool_id ?? node.model ?? "";
  return (
    <div
      className="flex items-center gap-2.5 border-t border-line-soft py-1.5 pr-3 first:border-t-0"
      style={{ paddingLeft: 12 + depth * 22 }}
    >
      <span
        className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide ${kind.look}`}
      >
        {kind.label}
      </span>
      <span className="shrink-0 font-mono text-[12.5px] font-medium">{node.id}</span>
      {what ? <span className="shrink-0 font-mono text-[11.5px] text-ink-4">{what}</span> : null}
      <span className="min-w-0 truncate text-[12px] text-ink-3" title={summary(node)}>
        {summary(node)}
      </span>
    </div>
  );
}

export function WorkflowOutline({ ir }: { ir: WorkflowIr }) {
  const rows: ReactNode[] = [];
  for (const node of ir.nodes) {
    rows.push(<Row key={node.id} node={node} depth={0} />);
    for (const inner of node.body ?? []) {
      rows.push(<Row key={`${node.id}.${inner.id}`} node={inner} depth={1} />);
    }
  }
  return <div className="flex flex-col">{rows}</div>;
}

/** `4 steps, then 6 per item` — đủ để biết cỡ của workflow mà không phải đếm. */
export function outlineCount(ir: WorkflowIr): string {
  const top = ir.nodes.filter((node) => node.type !== "trigger").length;
  const inLoops = ir.nodes.reduce((total, node) => total + (node.body?.length ?? 0), 0);
  const steps = `${top} ${top === 1 ? "step" : "steps"}`;
  return inLoops ? `${steps}, ${inLoops} inside a loop` : steps;
}

/** Khung có tiêu đề dùng chung cho thẻ giả định và trang draft. */
export function Panel({
  title,
  count,
  tone = "plain",
  children,
}: {
  title: string;
  count?: string;
  tone?: "plain" | "warn" | "error";
  children: ReactNode;
}) {
  const look = {
    plain: { border: "border-line", head: "bg-canvas text-ink-4" },
    warn: { border: "border-[#f2dfb8]", head: "bg-[#fff8ec] text-warn" },
    error: { border: "border-danger-line", head: "bg-danger-wash text-danger" },
  }[tone];
  return (
    <section className={`overflow-hidden rounded-[9px] border bg-surface ${look.border}`}>
      <header className={`flex items-center justify-between px-3 py-2 ${look.head}`}>
        <h3 className="text-[11px] font-semibold tracking-wide uppercase">{title}</h3>
        {count ? <span className="text-[11.5px]">{count}</span> : null}
      </header>
      {children}
    </section>
  );
}

/** Một dòng có chấm màu: cảnh báo, lỗi, kết quả kiểm. */
export function Notice({ tone, children }: { tone: "ok" | "warn" | "error"; children: ReactNode }) {
  const dot = { ok: "bg-ok", warn: "bg-warn", error: "bg-danger" }[tone];
  return (
    <div className="flex gap-2 border-t border-line-soft px-3 py-2 text-[12.5px] leading-relaxed text-ink-2">
      <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${dot}`} />
      <span className="min-w-0">{children}</span>
    </div>
  );
}
