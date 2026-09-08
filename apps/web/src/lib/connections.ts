import type { ConnectionStatus } from "@/lib/api";

/** Nhãn đọc được cho từng trạng thái kết nối — khớp `ConnectionStatus` ở backend. */
export const CONNECTION_STATUS_LABEL: Record<ConnectionStatus, string> = {
  active: "Active",
  expired: "Expired",
  revoked: "Revoked",
  missing_scope: "Missing scope",
  reconnect_required: "Reconnect required",
  disabled: "Paused",
};

/**
 * Tông màu (Tailwind classes) cho một huy hiệu trạng thái: xanh = tốt, xám =
 * đã tắt/thu hồi, hổ phách = cần người dùng xử lý.
 */
export function connectionStatusTone(status: ConnectionStatus): { badge: string; dot: string } {
  if (status === "active") return { badge: "bg-ok-wash text-ok", dot: "bg-ok" };
  if (status === "disabled" || status === "revoked")
    return { badge: "bg-sunken text-ink-3", dot: "bg-ink-4" };
  return { badge: "bg-warn-wash text-warn", dot: "bg-warn" };
}

/** `true` nếu kết nối không dùng được cho tới khi người dùng làm gì đó. */
export function isConnectionUsable(status: ConnectionStatus): boolean {
  return status === "active";
}
