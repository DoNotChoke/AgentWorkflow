"use client";

import { ProviderTile } from "@/components/provider-icon";
import { Button, ErrorNote } from "@/components/ui";
import type { ConnectionCandidate } from "@/lib/api";
import { useSetConversationConnection } from "@/lib/queries";

/**
 * Thẻ hỏi hiện trong dòng chat khi Agent Runtime báo `error_category` là
 * `MissingConnection` hay `ConnectionChoiceRequired` trên sự kiện SSE
 * `tool_call_completed` (ADR-0004) — thay cho một dòng lỗi chữ thường.
 *
 * Bấm chọn một ứng viên thì LƯU LỰA CHỌN rồi báo `onPicked` — nơi gắn
 * component này (màn chat, chưa dựng) tự quyết định có tự soạn và tự gửi một
 * tin nhắn trả lời thay người dùng hay không (giống cách trả lời một câu hỏi
 * dạng "AskUserQuestion": người dùng không phải gõ lại gì). Component này
 * không tự gửi tin nhắn nào — nó không biết gì về `Conversation`/`Message`,
 * chỉ biết ghi lựa chọn.
 */
export function ConnectionChoiceCard({
  organizationId,
  workspaceId,
  conversationId,
  category,
  provider,
  candidates,
  onPicked,
}: {
  organizationId: string | null;
  workspaceId: string | null;
  conversationId: string;
  category: "MissingConnection" | "ConnectionChoiceRequired";
  provider: string;
  /** Chỉ có khi `category` là `ConnectionChoiceRequired`. */
  candidates?: ConnectionCandidate[];
  onPicked?: (pickedLabel: string) => void;
}) {
  const setConnection = useSetConversationConnection(organizationId, workspaceId);
  const providerLabel = provider.charAt(0).toUpperCase() + provider.slice(1);

  if (category === "MissingConnection") {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-warn-line bg-warn-wash px-3.5 py-3">
        <ProviderTile provider={provider} box={28} />
        <span className="min-w-0 grow text-[13px] leading-relaxed text-ink-2">
          No {providerLabel} account connected yet.
        </span>
        <a href="/connections" className="shrink-0">
          <Button type="button" variant="secondary" className="h-8 px-3 text-xs whitespace-nowrap">
            Connect {providerLabel}
          </Button>
        </a>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-warn-line bg-warn-wash p-2.5">
      <span className="px-1 text-[11px] font-semibold tracking-wider text-warn uppercase">
        Choose a {providerLabel} account
      </span>
      {(candidates ?? []).map((candidate) => (
        <button
          key={candidate.connection_id}
          type="button"
          disabled={setConnection.isPending}
          onClick={async () => {
            await setConnection.mutateAsync({
              conversationId,
              provider,
              connectionId: candidate.connection_id,
            });
            onPicked?.(candidate.display_name);
          }}
          className="flex items-center gap-3 rounded-md border border-line bg-surface px-3 py-2.5 text-left transition-colors enabled:hover:border-line-strong disabled:opacity-55"
        >
          <ProviderTile provider={provider} box={28} />
          <span className="flex min-w-0 grow flex-col">
            <span className="truncate text-sm font-medium">{candidate.display_name}</span>
            {candidate.external_account_label ? (
              <span className="truncate text-[11.5px] text-ink-4">
                {candidate.external_account_label}
              </span>
            ) : null}
          </span>
          <span className="shrink-0 rounded bg-sunken px-2 py-0.5 text-[10.5px] font-medium text-ink-3 uppercase">
            {candidate.sharing === "workspace" ? "Workspace" : "Private"}
          </span>
        </button>
      ))}
      <ErrorNote error={setConnection.error} />
    </div>
  );
}
