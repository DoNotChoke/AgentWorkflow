"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiRequest, type CompileSession, type CompileSseEvent } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { streamSse } from "@/lib/sse";

/**
 * Một phiên biên dịch: biến một conversation thành workflow qua hai lần xác nhận.
 *
 * Ba thao tác gọi model (mở phiên, nói lại, chấp nhận ý đồ) là luồng SSE: vài
 * dòng `status` trong lúc chờ, rồi `session` mang phiên sau thao tác, hoặc
 * `error`. Bốn thao tác còn lại trả JSON ngay.
 *
 * Phiên sống ở backend, không ở đây. `start` hỏi backend xem người dùng có phiên
 * nào đang mở trên conversation này không rồi mới mở phiên mới — đóng cửa sổ
 * giữa chừng không làm mất việc đã làm, và không tốn thêm một lời gọi model.
 */

const OFFLINE_MESSAGE =
  "Lost the connection to the Agent Runtime. Check that the process on port 8001 is running.";

export function useCompileSession({
  organizationId,
  workspaceId,
  conversationId,
}: {
  organizationId: string | null;
  workspaceId: string | null;
  conversationId: string | null;
}) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();

  const [session, setSession] = useState<CompileSession | null>(null);
  /** Các dòng tiến độ của thao tác đang chạy, theo thứ tự backend phát. */
  const [progress, setProgress] = useState<string[]>([]);
  /** Lời người dùng vừa gửi, hiện ngay trong lúc model còn chạy. */
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runningRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const headers = useCallback((): Record<string, string> => {
    const found: Record<string, string> = {};
    if (organizationId) found["x-organization-id"] = organizationId;
    if (workspaceId) found["x-workspace-id"] = workspaceId;
    return found;
  }, [organizationId, workspaceId]);

  /** Chạy một thao tác có gọi model. Lỗi giữa chừng thì phiên giữ nguyên. */
  const stream = useCallback(
    async (path: string, body?: unknown) => {
      if (runningRef.current) return;
      runningRef.current = true;
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      setBusy(true);
      setError(null);
      setProgress([]);
      try {
        const events = streamSse(path, {
          method: "POST",
          accessToken,
          signal: abort.signal,
          headers: headers(),
          body,
        });
        for await (const raw of events) {
          const event = raw as CompileSseEvent;
          if (event.event === "status") {
            setProgress((lines) => [...lines, event.data.text]);
          } else if (event.event === "session") {
            setSession(event.data);
          } else if (event.event === "error") {
            setError(event.data.message);
          }
        }
      } catch (caught) {
        if (abort.signal.aborted) return;
        setError(caught instanceof ApiError ? caught.message : OFFLINE_MESSAGE);
      } finally {
        if (!abort.signal.aborted) {
          runningRef.current = false;
          setBusy(false);
          setSaid(null);
          setProgress([]);
        }
      }
    },
    [accessToken, headers],
  );

  /** Chạy một thao tác không gọi model. */
  const act = useCallback(
    async (action: "reopen-intent" | "confirm" | "abandon") => {
      if (!session || runningRef.current) return null;
      runningRef.current = true;
      setBusy(true);
      setError(null);
      try {
        const next = await apiRequest<CompileSession>(
          `/v1/compile-sessions/${session.id}/${action}`,
          { method: "POST", accessToken, headers: headers() },
        );
        setSession(next);
        return next;
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : OFFLINE_MESSAGE);
        return null;
      } finally {
        runningRef.current = false;
        setBusy(false);
      }
    },
    [accessToken, headers, session],
  );

  const start = useCallback(async () => {
    if (!conversationId || runningRef.current) return;
    setError(null);
    let open: CompileSession | null = null;
    try {
      open = await apiRequest<CompileSession | null>(
        `/v1/conversations/${conversationId}/compile-sessions/current`,
        { accessToken, headers: headers() },
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : OFFLINE_MESSAGE);
      return;
    }
    if (open) {
      setSession(open);
      return;
    }
    await stream(`/v1/conversations/${conversationId}/compile-sessions`);
  }, [accessToken, conversationId, headers, stream]);

  const say = useCallback(
    async (text: string) => {
      const body = text.trim();
      if (!session || !body) return;
      setSaid(body);
      await stream(`/v1/compile-sessions/${session.id}/messages`, { content: body });
    },
    [session, stream],
  );

  const acceptIntent = useCallback(async () => {
    if (!session) return;
    await stream(`/v1/compile-sessions/${session.id}/accept-intent`);
  }, [session, stream]);

  const confirm = useCallback(async () => {
    const done = await act("confirm");
    if (done) {
      await queryClient.invalidateQueries({ queryKey: queryKeys.workflows(organizationId, workspaceId) });
    }
    return done;
  }, [act, organizationId, queryClient, workspaceId]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    runningRef.current = false;
    setSession(null);
    setProgress([]);
    setSaid(null);
    setBusy(false);
    setError(null);
  }, []);

  return {
    session,
    progress,
    said,
    busy,
    error,
    start,
    say,
    acceptIntent,
    reopenIntent: () => act("reopen-intent"),
    confirm,
    abandon: () => act("abandon"),
    reset,
  };
}
