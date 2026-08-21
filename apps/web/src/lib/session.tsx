"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useMemo } from "react";
import { ApiError, apiRequest, type Session, type User } from "@/lib/api";

/**
 * Phiên làm việc phía client.
 *
 * Access token **chỉ nằm trong bộ nhớ** — không localStorage, không cookie đọc
 * được bằng script. Refresh token thì ngược lại: cookie httpOnly mà JavaScript
 * không chạm tới được.
 *
 * Hệ quả: tải lại trang là mất access token. Cách khôi phục là gọi
 * `POST /v1/auth/refresh` — cookie tự đi kèm và backend cấp token mới. Việc đó
 * là một lần đọc dữ liệu, nên nó là một query chứ không phải `useEffect` gọi
 * `setState`: query lo sẵn trạng thái chờ, khử trùng lặp, và không gây chuỗi
 * render nối tiếp.
 */

const SESSION_KEY = ["session"] as const;

type SessionStatus = "loading" | "authenticated" | "anonymous";

type SessionContextValue = {
  status: SessionStatus;
  user: User | null;
  accessToken: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, displayName: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

/** `null` = đã hỏi backend và chưa đăng nhập. `undefined` = còn đang hỏi. */
async function restoreSession(): Promise<Session | null> {
  try {
    return await apiRequest<Session>("/v1/auth/refresh", { method: "POST" });
  } catch (error) {
    // 401 ở đây là chuyện thường: chưa đăng nhập bao giờ, hoặc cookie hết hạn.
    // Chỉ lỗi khác mới đáng để nổi lên.
    if (error instanceof ApiError && error.isUnauthenticated) return null;
    throw error;
  }
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();

  const session = useQuery({
    queryKey: SESSION_KEY,
    queryFn: restoreSession,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    /**
     * Access token sống có hạn. Xoay vòng trước khi hết hạn một phút để không
     * có khoảng trống mà mọi request đều nhận 401.
     *
     * Đây chưa phải cơ chế đầy đủ: một request lỡ rơi đúng khe hở vẫn hỏng.
     * Bản hoàn chỉnh là bắt 401 rồi thử lại sau khi refresh, làm sau.
     */
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const msLeft = new Date(data.access_token_expires_at).getTime() - Date.now();
      return Math.max(msLeft - 60_000, 30_000);
    },
  });

  const signInMutation = useMutation({
    mutationFn: (input: { email: string; password: string }) =>
      apiRequest<Session>("/v1/auth/login", { method: "POST", body: input }),
    onSuccess: (data) => queryClient.setQueryData(SESSION_KEY, data),
  });

  const signUpMutation = useMutation({
    mutationFn: (input: { email: string; password: string; display_name: string }) =>
      apiRequest<Session>("/v1/auth/register", { method: "POST", body: input }),
    onSuccess: (data) => queryClient.setQueryData(SESSION_KEY, data),
  });

  const signOutMutation = useMutation({
    mutationFn: () => apiRequest<void>("/v1/auth/logout", { method: "POST" }),
    onSettled: () => {
      // Kể cả khi gọi hỏng vẫn phải đưa người dùng ra ngoài — giữ họ lại trong
      // một phiên mà chính họ vừa yêu cầu kết thúc là hành vi tệ hơn.
      queryClient.setQueryData(SESSION_KEY, null);
      // Dọn sạch cache để dữ liệu của tenant vừa rồi không hiện cho người sau.
      queryClient.removeQueries({ predicate: (query) => query.queryKey !== SESSION_KEY });
    },
  });

  const value = useMemo<SessionContextValue>(() => {
    const status: SessionStatus = session.isPending
      ? "loading"
      : session.data
        ? "authenticated"
        : "anonymous";

    return {
      status,
      user: session.data?.user ?? null,
      accessToken: session.data?.access_token ?? null,
      signIn: async (email, password) => {
        await signInMutation.mutateAsync({ email, password });
      },
      signUp: async (email, password, displayName) => {
        await signUpMutation.mutateAsync({ email, password, display_name: displayName });
      },
      signOut: async () => {
        await signOutMutation.mutateAsync();
      },
    };
  }, [session.isPending, session.data, signInMutation, signUpMutation, signOutMutation]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) throw new Error("useSession phải nằm bên trong <SessionProvider>.");
  return value;
}
