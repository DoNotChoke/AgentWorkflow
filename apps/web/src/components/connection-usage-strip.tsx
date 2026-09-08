"use client";

import { useState } from "react";
import { ProviderTile } from "@/components/provider-icon";
import type { Connection } from "@/lib/api";
import { CONNECTION_STATUS_LABEL, connectionStatusTone, isConnectionUsable } from "@/lib/connections";
import { useSetConversationConnection } from "@/lib/queries";

/** Một provider mà conversation này ĐÃ chốt lựa chọn, cùng các ứng viên khác
 * cùng provider để đổi sang (nơi dùng tự lọc `useConnections()` theo provider
 * — component này không tự gọi API liệt kê). */
export type ConnectionUsageRow = {
  provider: string;
  current: Connection;
  candidates: Connection[];
};

/**
 * Dải "Using" ngay dưới header của một cuộc chat — mỗi provider cuộc hội
 * thoại này ĐÃ thật sự chốt connection, không phải mọi provider Agent CÓ THỂ
 * dùng (đúng tinh thần resolve-khi-cần của ADR-0004: chưa chạm tới thì chưa
 * có gì để hiện). Bấm một chip để xem/đổi bất cứ lúc nào — khác
 * `ConnectionChoiceCard`, đổi ở đây KHÔNG tự gửi tin nhắn nào: đây không phải
 * trả lời một câu hỏi đang treo, chỉ là tự sửa cấu hình cho lần gọi tool sau.
 *
 * Lựa chọn đã chốt là "sticky" và KHÔNG được backend xác thực lại (ADR-0004
 * mục 5) — một connection bị thu hồi/tạm dừng vẫn nằm đây cho tới khi người
 * dùng đổi. Vì thế chip hiện rõ trạng thái khi nó không còn `active`, và menu
 * chỉ cho chọn sang connection đang dùng được.
 */
export function ConnectionUsageStrip({
  organizationId,
  workspaceId,
  conversationId,
  usage,
}: {
  organizationId: string | null;
  workspaceId: string | null;
  conversationId: string;
  usage: ConnectionUsageRow[];
}) {
  const [openProvider, setOpenProvider] = useState<string | null>(null);
  const setConnection = useSetConversationConnection(organizationId, workspaceId);

  if (usage.length === 0) return null;

  return (
    <div className="flex items-center gap-2 border-b border-line bg-canvas px-8 py-2">
      <span className="text-[10.5px] font-semibold tracking-wider text-ink-4 uppercase">Using</span>
      {usage.map((row) => {
        const broken = !isConnectionUsable(row.current.status);
        const tone = connectionStatusTone(row.current.status);
        // Không mời đổi sang một connection cũng đang hỏng — chỉ cái đang dùng
        // được mới là lựa chọn thật.
        const selectable = row.candidates.filter((c) => isConnectionUsable(c.status));

        return (
          <div key={row.provider} className="relative">
            <button
              type="button"
              onClick={() => setOpenProvider(openProvider === row.provider ? null : row.provider)}
              className={`flex h-[26px] items-center gap-1.5 rounded-md border bg-surface px-2 text-xs font-medium hover:bg-sunken ${
                broken ? "border-warn-line" : "border-line"
              }`}
            >
              <ProviderTile provider={row.provider} box={16} icon={11} />
              <span className="max-w-[140px] truncate">{row.current.display_name}</span>
              {broken ? (
                <span className={`rounded px-1 py-0.5 text-[9.5px] font-semibold uppercase ${tone.badge}`}>
                  {CONNECTION_STATUS_LABEL[row.current.status]}
                </span>
              ) : null}
              <IconChevronDown />
            </button>

            {openProvider === row.provider ? (
              <>
                {/* Nền trong suốt phủ cả màn — bấm ra ngoài để đóng dropdown. */}
                <div className="fixed inset-0 z-10" onClick={() => setOpenProvider(null)} />
                <div className="absolute top-8 left-0 z-20 flex min-w-[250px] flex-col gap-0.5 rounded-lg border border-line bg-surface p-1.5 shadow-lg">
                  {broken ? (
                    <p className="mx-1 mb-1 rounded-md border border-warn-line bg-warn-wash px-2.5 py-2 text-[12px] leading-snug text-ink-2">
                      This connection is {CONNECTION_STATUS_LABEL[row.current.status].toLowerCase()}.
                      {selectable.length > 0
                        ? " Pick another below,"
                        : " Reconnect it in Connections,"}{" "}
                      or the next tool call will fail.
                    </p>
                  ) : null}

                  {selectable.length === 0 && !broken ? (
                    <p className="px-2.5 py-2 text-[12px] text-ink-4">
                      No other connection for this provider.
                    </p>
                  ) : null}

                  {selectable.map((candidate) => {
                    const selected = candidate.id === row.current.id;
                    return (
                      <button
                        key={candidate.id}
                        type="button"
                        disabled={setConnection.isPending}
                        onClick={async () => {
                          await setConnection.mutateAsync({
                            conversationId,
                            provider: row.provider,
                            connectionId: candidate.id,
                          });
                          setOpenProvider(null);
                        }}
                        className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors disabled:opacity-55 ${
                          selected ? "bg-accent-wash" : "hover:bg-sunken"
                        }`}
                      >
                        <span className="min-w-0 grow truncate">{candidate.display_name}</span>
                        <span className="shrink-0 rounded bg-sunken px-1.5 py-0.5 text-[10px] font-medium text-ink-3 uppercase">
                          {candidate.sharing === "workspace" ? "Workspace" : "Private"}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function IconChevronDown() {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0 text-ink-4"
      aria-hidden
    >
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}
