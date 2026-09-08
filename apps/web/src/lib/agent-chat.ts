"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type AgentSseEvent,
  ApiError,
  type ChatMessage,
  type ConnectionCandidate,
  type ErrorCategory,
} from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { streamSse } from "@/lib/sse";

/**
 * Gửi một tin nhắn và đọc luồng SSE của lượt Agent.
 *
 * Model Gateway chưa stream cấp token, nên chi tiết tối đa là "một tool
 * bắt đầu/xong" và "câu trả lời cuối cùng" (xem `AgentEventKind` ở backend).
 *
 * Trong lúc một lượt đang chạy, hook giữ nó ở `pending` — màn chat vẽ tin
 * nhắn đã lưu (từ query) + lượt `pending` này nối đuôi. Khi lượt kết thúc
 * sạch (không lỗi, có câu trả lời), `pending` được xoá và query tin nhắn
 * được làm mới — lượt vừa rồi chuyển sang bản đã lưu, liền mạch.
 *
 * Khi lượt kết thúc *không* sạch — lỗi model, hoặc một tool báo cần chọn
 * connection (ADR-0004) — `pending` được GIỮ LẠI để thẻ lỗi/thẻ chọn còn
 * bấm được: bản đã lưu không mang `error_category` hay danh sách ứng viên.
 * `baselineIds` giữ cho tin nhắn của chính lượt đó (giờ đã có trong query
 * sau khi làm mới) không bị vẽ trùng với lượt `pending`.
 */

export type PendingToolCall = {
  toolId: string;
  status: "running" | "done" | "error";
  errorCategory?: ErrorCategory;
  provider?: string;
  candidates?: ConnectionCandidate[];
};

export type PendingTurn = {
  conversationId: string;
  /** Id các tin nhắn đã có trước lượt này — dùng để khử trùng lặp khi vẽ. */
  baselineIds: Set<string>;
  userText: string;
  calls: PendingToolCall[];
  text: string;
  /** Lỗi tầng model/hạ tầng (sự kiện SSE `error`), không phải một tool đơn lẻ. */
  error: string | null;
  done: boolean;
};

const OFFLINE_MESSAGE =
  "Mất kết nối tới Agent Runtime. Kiểm tra tiến trình cổng 8001 có đang chạy không.";

export function useAgentTurn({
  organizationId,
  workspaceId,
}: {
  organizationId: string | null;
  workspaceId: string | null;
}) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const runningRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    runningRef.current = false;
    setPending(null);
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback(
    (conversationId: string, text: string, currentMessages: ChatMessage[]) => {
      const body = text.trim();
      if (!conversationId || !body || runningRef.current) return;
      runningRef.current = true;

      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      const turn: PendingTurn = {
        conversationId,
        baselineIds: new Set(currentMessages.map((m) => m.id)),
        userText: body,
        calls: [],
        text: "",
        error: null,
        done: false,
      };
      const sync = () =>
        setPending({ ...turn, calls: turn.calls.map((c) => ({ ...c })) });
      sync();

      void (async () => {
        try {
          const stream = streamSse(`/v1/conversations/${conversationId}/messages`, {
            method: "POST",
            accessToken,
            signal: abort.signal,
            headers: {
              ...(organizationId ? { "x-organization-id": organizationId } : {}),
              ...(workspaceId ? { "x-workspace-id": workspaceId } : {}),
            },
            body: { content: body },
          });

          for await (const raw of stream) {
            const ev = raw as AgentSseEvent;
            if (ev.event === "tool_call_started") {
              turn.calls.push({ toolId: ev.data.tool_id, status: "running" });
            } else if (ev.event === "tool_call_completed") {
              // started/completed xen kẽ nghiêm ngặt theo từng call (run_turn),
              // nên `completed` cập nhật call `running` gần nhất.
              for (let i = turn.calls.length - 1; i >= 0; i--) {
                if (turn.calls[i].status === "running") {
                  turn.calls[i] = {
                    ...turn.calls[i],
                    status: ev.data.is_error ? "error" : "done",
                    errorCategory: ev.data.error_category,
                    provider: ev.data.error_details?.provider,
                    candidates: ev.data.error_details?.candidates,
                  };
                  break;
                }
              }
            } else if (ev.event === "final") {
              turn.text = ev.data.text;
            } else if (ev.event === "error") {
              turn.error = ev.data.message;
            }
            sync();
          }
        } catch (err) {
          if (abort.signal.aborted) return;
          turn.error =
            err instanceof ApiError ? err.message : OFFLINE_MESSAGE;
        } finally {
          if (!abort.signal.aborted) {
            turn.done = true;
            sync();
            runningRef.current = false;

            await queryClient.invalidateQueries({
              queryKey: queryKeys.messages(organizationId, conversationId),
            });
            await queryClient.invalidateQueries({
              queryKey: ["conversations", organizationId],
            });
            // Một connection có thể vừa được tự chọn trong im lặng (ADR-0004:
            // đúng 1 ứng viên) — dải "Using" đọc lại từ đây.
            await queryClient.invalidateQueries({
              queryKey: queryKeys.conversationConnections(organizationId, conversationId),
            });

            const clean =
              turn.error === null &&
              turn.text.length > 0 &&
              turn.calls.every((c) => c.status === "done");
            if (clean) setPending(null);
          }
        }
      })();
    },
    [accessToken, organizationId, workspaceId, queryClient],
  );

  return { pending, send, clear, isRunning: pending !== null && !pending.done };
}
