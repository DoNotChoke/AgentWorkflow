/**
 * Logo của từng provider.
 *
 * Dùng đúng nhãn hiệu của họ chứ không phải một chữ cái trong ô màu: người dùng
 * nhận ra Slack và Google trước khi kịp đọc chữ, và một kết nối trỏ nhầm tài
 * khoản là loại nhầm lẫn đắt. Logo nhiều màu nên nó ngồi trên nền trắng viền
 * nhạt (`ProviderTile`) thay vì nền có tông.
 *
 * Provider lạ — chưa có logo ở đây — rơi về một ô chữ cái. Danh mục provider
 * do backend quyết, nên màn hình không được vỡ khi ai đó thêm một cái mới.
 */

function Slack({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 122.8 122.8" aria-hidden>
      <path
        d="M25.8 77.6a12.9 12.9 0 1 1-12.9-12.9h12.9zM32.3 77.6a12.9 12.9 0 0 1 25.8 0v32.3a12.9 12.9 0 0 1-25.8 0z"
        fill="#E01E5A"
      />
      <path
        d="M45.2 25.8a12.9 12.9 0 1 1 12.9-12.9v12.9zM45.2 32.3a12.9 12.9 0 0 1 0 25.8H12.9a12.9 12.9 0 0 1 0-25.8z"
        fill="#36C5F0"
      />
      <path
        d="M97 45.2a12.9 12.9 0 1 1 12.9 12.9H97zM90.5 45.2a12.9 12.9 0 0 1-25.8 0V12.9a12.9 12.9 0 0 1 25.8 0z"
        fill="#2EB67D"
      />
      <path
        d="M77.6 97a12.9 12.9 0 1 1-12.9 12.9V97zM77.6 90.5a12.9 12.9 0 0 1 0-25.8h32.3a12.9 12.9 0 0 1 0 25.8z"
        fill="#ECB22E"
      />
    </svg>
  );
}

function Google({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      <path
        d="M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z"
        fill="#4285F4"
      />
      <path
        d="M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z"
        fill="#34A853"
      />
      <path
        d="M11.69 28.18C11.25 26.86 11 25.45 11 24s.25-2.86.69-4.18v-5.7H4.34A21.99 21.99 0 0 0 2 24c0 3.55.85 6.91 2.34 9.88z"
        fill="#FBBC05"
      />
      <path
        d="M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z"
        fill="#EA4335"
      />
    </svg>
  );
}

function Notion({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      <path
        d="M9.6 8.3 30.9 6.7c2.6-.2 3.3-.1 4.9 1.1l6.8 4.8c1.1.8 1.5 1 1.5 1.9v26c0 1.6-.6 2.5-2.6 2.7l-24.7 1.5c-1.6.1-2.3-.1-3.1-1.1l-5-6.5c-.9-1.2-1.3-2-1.3-3.1V10.9c0-1.3.6-2.4 2.2-2.6z"
        fill="#fff"
      />
      <path
        d="M30.9 6.7 9.6 8.3C8 8.5 7.4 9.6 7.4 10.9v22.2c0 1.1.4 1.9 1.3 3.1l5 6.5c.8 1 1.5 1.2 3.1 1.1l24.7-1.5c2-.2 2.6-1.1 2.6-2.7v-26c0-.8-.3-1.1-1.3-1.8l-7-4.9c-1.6-1.2-2.3-1.3-4.9-1.1zM17.6 12.5c-1.9.1-2.4.2-3.4-.7l-2.6-2c-.3-.3-.1-.6.6-.7l20.5-1.5c1.6-.1 2.5.5 3.1 1l3.1 2.2c.1.1.5.6 0 .6l-21.2 1.3zm-2.3 25.6V15.8c0-1 .3-1.4 1.2-1.5l24.3-1.4c.8-.1 1.2.4 1.2 1.4v22.2c0 1-.2 1.8-1.5 1.9l-23.3 1.4c-1.3.1-1.9-.3-1.9-1.7zm23-21.1c.1.7 0 1.3-.7 1.4l-1.1.2v16.5c-1 .5-1.9.8-2.6.8-1.2 0-1.5-.4-2.4-1.5l-7.3-11.5v11.1l2.3.5s0 1.4-1.9 1.4l-5.2.3c-.2-.3 0-1.1.5-1.2l1.4-.4V19.7l-1.9-.2c-.2-.7.2-1.7 1.3-1.8l5.6-.4 7.7 11.8V18.6l-2-.2c-.2-.9.5-1.5 1.3-1.6l5-.3z"
        fill="#0F0F0F"
      />
    </svg>
  );
}

export function ProviderIcon({ provider, size = 19 }: { provider: string; size?: number }) {
  if (provider === "slack") return <Slack size={size} />;
  if (provider === "google") return <Google size={size} />;
  if (provider === "notion") return <Notion size={size} />;
  return (
    <span
      className="font-semibold text-ink-3 uppercase"
      style={{ fontSize: Math.round(size * 0.62) }}
    >
      {provider.slice(0, 1)}
    </span>
  );
}

/** Ô nền trắng để logo nhiều màu tự đứng được trên nền sáng của ứng dụng. */
export function ProviderTile({
  provider,
  box = 36,
  icon,
}: {
  provider: string;
  box?: number;
  icon?: number;
}) {
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-lg border border-line bg-surface"
      style={{ width: box, height: box }}
    >
      <ProviderIcon provider={provider} size={icon ?? Math.round(box * 0.53)} />
    </span>
  );
}
