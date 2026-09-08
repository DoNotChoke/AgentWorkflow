"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ConnectionChoiceCard } from "@/components/connection-choice-card";
import { ConnectionUsageStrip, type ConnectionUsageRow } from "@/components/connection-usage-strip";
import { Markdown } from "@/components/markdown";
import { ProviderTile } from "@/components/provider-icon";
import { ErrorNote } from "@/components/ui";
import type { Agent, ChatMessage, ToolSummary } from "@/lib/api";
import { type PendingTurn, useAgentTurn } from "@/lib/agent-chat";
import { modelLabel } from "@/lib/models";
import {
  useConnections,
  useConversationConnections,
  useConversationMessages,
  useConversations,
  useCreateConversation,
  useTools,
} from "@/lib/queries";
import { initials } from "@/lib/name";
import { useTenant } from "@/lib/tenant";

/**
 * Màn chat với một Agent: danh sách hội thoại · luồng tin nhắn · ô soạn.
 *
 * Model Gateway chưa stream cấp token, nên tiến độ chỉ tới mức "một tool
 * bắt đầu/xong" và "câu trả lời cuối" — không phải từng chữ một.
 */
export function AgentChat({
  agent,
  conversationId,
  onBack,
  onSelectConversation,
}: {
  agent: Agent;
  conversationId: string | null;
  onBack: () => void;
  onSelectConversation: (conversationId: string | null) => void;
}) {
  const { organization, workspace } = useTenant();
  const orgId = organization.id;
  const wsId = workspace?.id ?? null;

  const conversations = useConversations(orgId, wsId, agent.id);
  const createConversation = useCreateConversation(orgId, wsId);
  const turn = useAgentTurn({ organizationId: orgId, workspaceId: wsId });

  // Một hội thoại vừa tạo được `send` ngay, trước khi URL kịp đi vòng về —
  // trong khe đó lượt `pending` chính là thứ nói ta đang ở hội thoại nào.
  const activeConvo = conversationId ?? turn.pending?.conversationId ?? null;

  const messages = useConversationMessages(orgId, wsId, activeConvo);
  const connections = useConnections(orgId, wsId);
  const convoConnections = useConversationConnections(orgId, wsId, activeConvo);

  const [composer, setComposer] = useState("");
  const threadRef = useRef<HTMLDivElement>(null);

  const persisted = useMemo(() => messages.data ?? [], [messages.data]);
  const pending =
    turn.pending && turn.pending.conversationId === activeConvo ? turn.pending : null;
  const visiblePersisted = pending
    ? persisted.filter((m) => pending.baselineIds.has(m.id))
    : persisted;
  const rendered = useMemo(() => foldMessages(visiblePersisted), [visiblePersisted]);

  // Cuộn xuống đáy khi có tin nhắn mới hoặc lượt đang chạy nhả sự kiện.
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [rendered.length, pending?.calls.length, pending?.text, pending?.error]);

  const usage: ConnectionUsageRow[] = useMemo(() => {
    const rows = connections.data ?? [];
    return (convoConnections.data ?? [])
      .map((sel) => {
        const candidates = rows.filter((c) => c.provider === sel.provider);
        const current = candidates.find((c) => c.id === sel.connection_id);
        return current ? { provider: sel.provider, current, candidates } : null;
      })
      .filter((row): row is ConnectionUsageRow => row !== null);
  }, [connections.data, convoConnections.data]);

  function selectConversation(id: string | null) {
    turn.clear();
    onSelectConversation(id);
  }

  async function submitMessage() {
    const body = composer.trim();
    if (!body || turn.isRunning || createConversation.isPending) return;
    setComposer("");

    if (!activeConvo) {
      const convo = await createConversation.mutateAsync(agent.id);
      onSelectConversation(convo.id);
      turn.send(convo.id, body, []);
    } else {
      turn.send(activeConvo, body, persisted);
    }
  }

  // Người dùng chọn một tài khoản từ thẻ "cần chọn": lựa chọn đã được lưu bên
  // trong thẻ; ở đây tự gửi một tin nhắn ngắn thay họ để lượt kế tiếp chạy
  // được — giống trả lời một câu hỏi treo (ADR-0004).
  function onConnectionPicked(label: string) {
    if (activeConvo) turn.send(activeConvo, `Use ${label}.`, persisted);
  }

  const convoList = conversations.data ?? [];
  const agentInitials = initials(agent.name);
  const toolCount = agent.enabled_tool_ids.length;
  const toolLabel = toolCount === 0 ? "no tools" : toolCount === 1 ? "1 tool" : `${toolCount} tools`;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b border-line px-8">
        <button
          type="button"
          onClick={onBack}
          className="-ml-2 flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] text-ink-3 hover:bg-sunken"
        >
          <IconChevronLeft />
          Agents
        </button>
        <span className="text-line-strong">/</span>
        <div className="flex flex-col">
          <span className="text-[15px] font-semibold">{agent.name}</span>
          <span className="flex items-center gap-1 font-mono text-[12px] text-ink-4">
            {modelLabel(agent.model)} ·<ToolsMenu agent={agent} label={toolLabel} />
          </span>
        </div>
      </header>

      {usage.length > 0 && activeConvo ? (
        <ConnectionUsageStrip
          organizationId={orgId}
          workspaceId={wsId}
          conversationId={activeConvo}
          usage={usage}
        />
      ) : null}

      <div className="flex min-h-0 grow">
        {/* danh sách hội thoại */}
        <div className="flex w-[260px] shrink-0 flex-col border-r border-line bg-canvas">
          <div className="p-3.5 pb-2">
            <button
              type="button"
              onClick={() => selectConversation(null)}
              className="flex h-9 w-full items-center justify-center gap-1.5 rounded-control border border-line-strong bg-surface text-[13px] font-medium hover:bg-sunken"
            >
              <IconPlus />
              New conversation
            </button>
          </div>
          <div className="flex min-h-0 grow flex-col gap-0.5 overflow-y-auto px-2.5 pb-2.5">
            {conversations.isPending ? (
              <span className="px-2 py-2 text-[12.5px] text-ink-4">Loading…</span>
            ) : convoList.length === 0 && !activeConvo ? (
              <p className="px-2 py-2 text-[12.5px] leading-relaxed text-ink-4">
                No conversations yet — send a message to start one.
              </p>
            ) : (
              convoList.map((convo) => {
                const active = convo.id === activeConvo;
                return (
                  <button
                    key={convo.id}
                    type="button"
                    onClick={() => selectConversation(convo.id)}
                    className={`flex flex-col gap-0.5 rounded-md border px-2.5 py-2 text-left transition-colors ${
                      active
                        ? "border-line bg-surface"
                        : "border-transparent hover:bg-sunken"
                    }`}
                  >
                    <span
                      className={`truncate text-[13px] ${active ? "font-semibold" : ""}`}
                    >
                      {conversationTitle(convo.id, persisted, activeConvo)}
                    </span>
                    <span className="text-[11.5px] text-ink-4">
                      {relativeTime(convo.updated_at)}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* luồng tin nhắn + ô soạn */}
        <div className="flex min-w-0 grow flex-col">
          <div ref={threadRef} className="flex min-h-0 grow flex-col gap-5 overflow-y-auto px-8 py-7">
            {messages.isError ? <ErrorNote error={messages.error} /> : null}

            {rendered.length === 0 && !pending ? (
              <div className="flex grow flex-col items-center justify-center gap-1.5 text-center">
                <h3 className="text-[17px] font-semibold">Start the conversation</h3>
                <p className="max-w-sm text-[13.5px] leading-relaxed text-ink-4">
                  Ask {agent.name} to do something — it can use {toolLabel}.
                </p>
              </div>
            ) : null}

            {rendered.map((turnItem) =>
              turnItem.kind === "user" ? (
                <UserBubble key={turnItem.id} text={turnItem.text} />
              ) : (
                <AssistantBubble
                  key={turnItem.id}
                  initials={agentInitials}
                  text={turnItem.text}
                  calls={turnItem.calls.map((c) => ({
                    toolId: c.toolId,
                    status: c.status,
                  }))}
                />
              ),
            )}

            {pending ? (
              <PendingBubble
                initials={agentInitials}
                pending={pending}
                organizationId={orgId}
                workspaceId={wsId}
                conversationId={activeConvo}
                onPicked={onConnectionPicked}
              />
            ) : null}
          </div>

          <div className="shrink-0 px-8 pt-3 pb-6">
            <div className="flex items-end gap-2.5 rounded-xl border border-line-strong bg-surface py-2 pr-2 pl-3.5 focus-within:border-accent">
              <textarea
                value={composer}
                onChange={(event) => setComposer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitMessage();
                  }
                }}
                rows={1}
                placeholder={`Message ${agent.name}…`}
                className="max-h-32 grow resize-none bg-transparent py-2 text-[15px] leading-relaxed outline-none placeholder:text-ink-4"
              />
              <button
                type="button"
                onClick={() => void submitMessage()}
                disabled={composer.trim().length === 0 || turn.isRunning}
                aria-label="Send"
                className="flex size-9 shrink-0 items-center justify-center rounded-control bg-ink text-white disabled:opacity-40"
              >
                <IconSend />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Danh sách tool của Agent ───────────────────────────────────────────────

/**
 * Nút "N tools" ở header — bấm để xem chính xác agent này được gọi những tool
 * nào, kèm mô tả và nhãn đọc/ghi, nhóm theo provider. Danh mục lấy từ
 * `useTools()` (GET /v1/tools); tool_id nào không còn trong danh mục thì vẫn
 * hiện id thô — whitelist của agent mới là nguồn chân lý.
 */
function ToolsMenu({ agent, label }: { agent: Agent; label: string }) {
  const [open, setOpen] = useState(false);
  const tools = useTools();

  const enabled = agent.enabled_tool_ids;
  if (enabled.length === 0) return <span>{label}</span>;

  const catalog = new Map((tools.data ?? []).map((t) => [t.tool_id, t]));
  const groups = groupToolsByProvider(enabled, catalog);

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-0.5 rounded px-0.5 text-ink-3 hover:text-ink hover:underline"
        aria-expanded={open}
      >
        {label}
        <IconCaret open={open} />
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute top-6 left-0 z-30 max-h-[60vh] w-[340px] overflow-y-auto rounded-lg border border-line bg-surface p-2 font-sans shadow-lg">
            {tools.isPending ? (
              <p className="px-2 py-2 text-[12.5px] text-ink-4">Loading catalogue…</p>
            ) : (
              groups.map((group) => (
                <div key={group.provider} className="flex flex-col gap-1 px-1 py-1">
                  <span className="flex items-center gap-1.5 px-1 text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
                    <ProviderTile provider={group.provider} box={15} icon={10} />
                    {group.provider}
                  </span>
                  {group.tools.map((tool) => (
                    <div key={tool.tool_id} className="flex flex-col gap-0.5 rounded-md px-1.5 py-1.5 hover:bg-sunken">
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-[12px] font-medium">{tool.tool_id}</span>
                        {tool.summary ? (
                          <span
                            className={`rounded px-1 py-0.5 text-[9.5px] font-semibold uppercase ${
                              tool.summary.side_effect === "write"
                                ? "bg-warn-wash text-warn"
                                : "bg-sunken text-ink-3"
                            }`}
                          >
                            {tool.summary.side_effect === "write" ? "Write" : "Read"}
                          </span>
                        ) : null}
                      </span>
                      <span className="text-[12px] leading-snug text-ink-3">
                        {tool.summary?.description ?? "Not in the current tool catalogue."}
                      </span>
                    </div>
                  ))}
                </div>
              ))
            )}
          </div>
        </>
      ) : null}
    </span>
  );
}

function groupToolsByProvider(
  toolIds: string[],
  catalog: Map<string, ToolSummary>,
): { provider: string; tools: { tool_id: string; summary: ToolSummary | undefined }[] }[] {
  const byProvider = new Map<string, { tool_id: string; summary: ToolSummary | undefined }[]>();
  for (const toolId of [...toolIds].sort()) {
    const summary = catalog.get(toolId);
    const provider = summary?.provider ?? toolId.split(".")[0];
    const list = byProvider.get(provider) ?? [];
    list.push({ tool_id: toolId, summary });
    byProvider.set(provider, list);
  }
  return [...byProvider.entries()]
    .map(([provider, list]) => ({ provider, tools: list }))
    .sort((a, b) => a.provider.localeCompare(b.provider));
}

function IconCaret({ open }: { open: boolean }) {
  return (
    <svg
      width="9"
      height="9"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`transition-transform ${open ? "rotate-180" : ""}`}
      aria-hidden
    >
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}

// ─── Tin nhắn ───────────────────────────────────────────────────────────────

type RenderCall = { callId: string; toolId: string; status: "running" | "done" | "error" };
type RenderTurn =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string; calls: RenderCall[] };

/**
 * Gộp `Message` đã lưu về các bong bóng để vẽ: message vai trò `tool` được
 * nhập vào tool call tương ứng của assistant (khớp `tool_call_id` ↔ `call_id`).
 * Nội dung message `tool` bắt đầu bằng "Lỗi:" là một lời gọi thất bại (xem
 * `_execute_tool_call` ở backend).
 */
function foldMessages(messages: ChatMessage[]): RenderTurn[] {
  const turns: RenderTurn[] = [];
  const callsById = new Map<string, RenderCall>();

  for (const message of messages) {
    if (message.role === "user") {
      turns.push({ kind: "user", id: message.id, text: message.content });
    } else if (message.role === "assistant") {
      const calls: RenderCall[] = message.tool_calls.map((tc) => {
        const call: RenderCall = { callId: tc.call_id, toolId: tc.tool_id, status: "done" };
        callsById.set(tc.call_id, call);
        return call;
      });
      turns.push({ kind: "assistant", id: message.id, text: message.content, calls });
    } else {
      const call = message.tool_call_id ? callsById.get(message.tool_call_id) : undefined;
      if (call) call.status = message.content.startsWith("Lỗi:") ? "error" : "done";
    }
  }
  return turns;
}

function conversationTitle(
  convoId: string,
  activeMessages: ChatMessage[],
  activeConvo: string | null,
): string {
  // Backend chưa lưu tiêu đề hội thoại — với hội thoại đang mở thì lấy tạm
  // câu đầu của người dùng; những cái khác chỉ có "Conversation" + thời gian.
  if (convoId === activeConvo) {
    const firstUser = activeMessages.find((m) => m.role === "user");
    if (firstUser) {
      return firstUser.content.length > 44
        ? `${firstUser.content.slice(0, 44)}…`
        : firstUser.content;
    }
  }
  return "Conversation";
}

function relativeTime(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[70%] self-end">
      <div className="rounded-xl bg-ink px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap text-white">
        {text}
      </div>
    </div>
  );
}

function AssistantBubble({
  initials: agentInitials,
  text,
  calls,
  children,
}: {
  initials: string;
  text: string;
  calls: { toolId: string; status: "running" | "done" | "error" }[];
  children?: React.ReactNode;
}) {
  return (
    <div className="flex max-w-[80%] gap-2.5 self-start">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md border border-accent-line bg-accent-wash text-[11px] font-semibold text-accent">
        {agentInitials}
      </span>
      <div className="flex min-w-0 flex-col gap-2 pt-0.5">
        {calls.map((call, index) => (
          <ToolChip key={index} toolId={call.toolId} status={call.status} />
        ))}
        {children}
        {text ? <Markdown>{text}</Markdown> : null}
      </div>
    </div>
  );
}

function PendingBubble({
  initials: agentInitials,
  pending,
  organizationId,
  workspaceId,
  conversationId,
  onPicked,
}: {
  initials: string;
  pending: PendingTurn;
  organizationId: string | null;
  workspaceId: string | null;
  conversationId: string | null;
  onPicked: (label: string) => void;
}) {
  const thinking =
    !pending.done && pending.text.length === 0 && pending.calls.every((c) => c.status !== "running");
  const showDots = !pending.done && pending.calls.length === 0 && pending.text.length === 0;

  return (
    <>
      <UserBubble text={pending.userText} />
      <div className="flex max-w-[80%] gap-2.5 self-start">
        <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md border border-accent-line bg-accent-wash text-[11px] font-semibold text-accent">
          {agentInitials}
        </span>
        <div className="flex min-w-0 flex-col gap-2 pt-0.5">
          {pending.calls.map((call, index) => (
            <div key={index} className="flex flex-col gap-2">
              <ToolChip toolId={call.toolId} status={call.status} />
              {conversationId &&
              (call.errorCategory === "MissingConnection" ||
                call.errorCategory === "ConnectionChoiceRequired") ? (
                <ConnectionChoiceCard
                  organizationId={organizationId}
                  workspaceId={workspaceId}
                  conversationId={conversationId}
                  category={call.errorCategory}
                  provider={call.provider ?? call.toolId.split(".")[0]}
                  candidates={call.candidates}
                  onPicked={onPicked}
                />
              ) : null}
            </div>
          ))}

          {showDots || thinking ? (
            <div className="flex gap-1 py-1.5">
              <Dot delay={0} />
              <Dot delay={150} />
              <Dot delay={300} />
            </div>
          ) : null}

          {pending.text ? <Markdown>{pending.text}</Markdown> : null}

          {pending.error ? (
            <p className="rounded-md border border-danger-line bg-danger-wash px-3 py-2 text-[13px] text-danger">
              {pending.error}
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}

function ToolChip({
  toolId,
  status,
}: {
  toolId: string;
  status: "running" | "done" | "error";
}) {
  const provider = toolId.split(".")[0];
  const dot =
    status === "running"
      ? "bg-ink-4 animate-pulse"
      : status === "error"
        ? "bg-warn"
        : "bg-ok";
  const label = status === "running" ? "Running…" : status === "error" ? "Failed" : "Done";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-2.5 py-1.5">
      <span className={`size-1.5 shrink-0 rounded-full ${dot}`} />
      <ProviderTile provider={provider} box={16} icon={11} />
      <span className="font-mono text-[12px]">{toolId}</span>
      <span className="grow" />
      <span className="text-[11.5px] text-ink-4">{label}</span>
    </div>
  );
}

function Dot({ delay }: { delay: number }) {
  return (
    <span
      className="size-1.5 animate-bounce rounded-full bg-ink-4"
      style={{ animationDelay: `${delay}ms` }}
    />
  );
}

// ─── Icons ──────────────────────────────────────────────────────────────────

function IconChevronLeft() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M10 3L5 8l5 5" />
    </svg>
  );
}

function IconPlus() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden>
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

function IconSend() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M8 13V3M4 7l4-4 4 4" />
    </svg>
  );
}
