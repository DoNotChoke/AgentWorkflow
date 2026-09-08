"use client";

import { useSyncExternalStore } from "react";

/**
 * Trạng thái "thu gọn sidebar" — lưu ở `localStorage`, chia sẻ cho mọi
 * component đọc nó, và đồng bộ giữa các tab.
 *
 * Dùng `useSyncExternalStore` thay vì `useState` + `useEffect`: server luôn
 * render ở trạng thái mở (snapshot `false`), client đọc lại từ localStorage
 * sau hydration — không lệch, không setState-trong-effect.
 */

const KEY = "aw:nav-collapsed";
const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function toggleNavCollapsed(): void {
  const next = !read();
  try {
    window.localStorage.setItem(KEY, next ? "1" : "0");
  } catch {
    // Không lưu được (private mode) thì lựa chọn chỉ sống trong phiên này.
  }
  for (const listener of listeners) listener();
}

export function useNavCollapsed(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      listeners.add(onChange);
      const onStorage = (event: StorageEvent) => {
        if (event.key === KEY) onChange();
      };
      window.addEventListener("storage", onStorage);
      return () => {
        listeners.delete(onChange);
        window.removeEventListener("storage", onStorage);
      };
    },
    read,
    () => false,
  );
}
