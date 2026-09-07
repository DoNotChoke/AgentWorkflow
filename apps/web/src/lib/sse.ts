/**
 * Đọc một luồng SSE từ Agent Runtime (`POST /v1/conversations/{id}/messages`).
 *
 * Không dùng `EventSource` gốc: endpoint là `POST` kèm header `Authorization`
 * để forward danh tính người dùng (§2.4) — hai thứ `EventSource` không hỗ trợ
 * (nó chỉ `GET`, không tự thêm header tuỳ ý). Tự parse thủ công theo khối cách
 * nhau bởi một dòng trống, mỗi khối có `event:`/`data:` — cùng hình dạng
 * `_sse_events()` dùng để test ở backend (`test_agent_entrypoint_http.py`),
 * chỉ khác là đọc dần theo luồng thay vì parse nguyên chuỗi một lần.
 */

import { ApiError } from "@/lib/api";

export type SseEvent = { event: string; data: unknown };

export async function* streamSse(
  path: string,
  init: {
    method?: string;
    body?: unknown;
    accessToken?: string | null;
    headers?: Record<string, string>;
    signal?: AbortSignal;
  } = {},
): AsyncGenerator<SseEvent> {
  const response = await fetch(path, {
    method: init.method ?? "POST",
    headers: {
      "content-type": "application/json",
      ...(init.accessToken ? { authorization: `Bearer ${init.accessToken}` } : {}),
      ...init.headers,
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init.signal,
  });

  if (!response.ok || !response.body) {
    // Lỗi trước khi luồng kịp mở (ví dụ 401/403/429) — vẫn hình dạng
    // `AppError.to_payload()` như mọi lỗi khác, đọc được qua `ApiError`.
    const text = await response.text().catch(() => "");
    let payload: unknown = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      // Không phải JSON (proxy chen vào) — ApiError tự có câu dự phòng.
    }
    throw new ApiError(response.status, payload as ConstructorParameters<typeof ApiError>[1]);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);

      const lines = block.split("\n");
      const eventLine = lines.find((line) => line.startsWith("event:"));
      const dataLine = lines.find((line) => line.startsWith("data:"));
      if (!eventLine) continue;

      const rawData = dataLine ? dataLine.slice("data:".length).trim() : "";
      yield {
        event: eventLine.slice("event:".length).trim(),
        data: rawData ? JSON.parse(rawData) : {},
      };
    }
  }
}
