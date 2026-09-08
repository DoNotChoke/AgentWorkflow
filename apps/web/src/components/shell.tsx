"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { NewOrganizationDialog } from "@/components/new-organization-dialog";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";
import { initials } from "@/lib/name";
import { toggleNavCollapsed, useNavCollapsed } from "@/lib/nav-collapsed";
import { useTenant } from "@/lib/tenant";

/**
 * Vỏ ứng dụng: rail organization · sidebar · khung chính.
 *
 * Sidebar đổi theo nơi đang đứng — điều hướng ứng dụng ở ngoài, mục lục Settings
 * khi vào Settings. Thiết kế cho Settings chiếm luôn cột đó chứ không lồng thêm
 * một cột nữa, nên vỏ chọn sidebar theo đường dẫn.
 *
 * Panel ngữ cảnh bên phải do từng màn tự dựng — nội dung của nó là chuyện của
 * màn đó, không phải của vỏ.
 */

type NavItem = { href: string; label: string; icon: React.ReactNode };

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { organizations, organization, selectOrganization } = useTenant();
  const [creatingOrganization, setCreatingOrganization] = useState(false);
  const collapsed = useNavCollapsed();

  // Settings có điều hướng dọc riêng và không có icon cho từng mục, nên phần
  // thu gọn chỉ áp cho sidebar ứng dụng.
  const isSettings = pathname.startsWith("/settings");
  const railCollapsed = collapsed && !isSettings;

  return (
    <div className="flex h-full min-h-0 w-full">
      {/* Rail: đổi organization */}
      <div className="flex w-[72px] shrink-0 flex-col items-center gap-2 border-r border-[#dcdcd7] bg-rail py-4">
        {organizations.map((org) => {
          const active = org.id === organization.id;
          return (
            <button
              key={org.id}
              type="button"
              title={org.name}
              onClick={() => selectOrganization(org.id)}
              className={`flex size-11 items-center justify-center rounded-[11px] text-sm font-semibold transition-colors ${
                active
                  ? "border-[1.5px] border-accent bg-surface text-accent"
                  : "bg-[#d9d9d4] font-medium text-ink-3 hover:bg-[#cfcfca]"
              }`}
            >
              {initials(org.name)}
            </button>
          );
        })}

        <button
          type="button"
          title="New organisation"
          aria-label="New organisation"
          onClick={() => setCreatingOrganization(true)}
          className="flex size-11 items-center justify-center rounded-[11px] border border-dashed border-line-strong text-ink-4 transition-colors hover:border-accent hover:text-accent"
        >
          <IconPlus />
        </button>
      </div>

      <div
        className={`flex shrink-0 flex-col border-r border-line bg-sidebar transition-[width] duration-150 ${
          railCollapsed ? "w-[60px]" : "w-[280px]"
        }`}
      >
        {isSettings ? (
          <SettingsSidebar />
        ) : (
          <AppSidebar collapsed={railCollapsed} onToggleCollapse={toggleNavCollapsed} />
        )}
      </div>

      <main className="min-w-0 grow overflow-auto bg-surface">{children}</main>

      {creatingOrganization ? (
        <NewOrganizationDialog
          onClose={() => setCreatingOrganization(false)}
          onCreated={(organizationId) => {
            setCreatingOrganization(false);
            // Vừa tạo xong thì chuyển sang luôn — đứng lại ở organization cũ
            // sau một hành động tạo là mất dấu thứ mình vừa làm.
            selectOrganization(organizationId);
          }}
        />
      ) : null}
    </div>
  );
}

function AppSidebar({
  collapsed,
  onToggleCollapse,
}: {
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const pathname = usePathname();
  const { organization } = useTenant();

  const nav: NavItem[] = [
    { href: "/agent", label: "Agent", icon: <IconChat /> },
    { href: "/workflows", label: "Workflows", icon: <IconNodes /> },
    { href: "/runs", label: "Runs", icon: <IconRun /> },
    { href: "/connections", label: "Connections", icon: <IconPlug /> },
    { href: "/settings/workspaces", label: "Settings", icon: <IconGear /> },
  ];

  return (
    <>
      <div
        className={`flex h-16 shrink-0 items-center border-b border-[#e9e9e6] ${
          collapsed ? "justify-center px-0" : "px-4"
        }`}
      >
        {collapsed ? (
          <span
            title={organization.name}
            className="flex size-8 items-center justify-center rounded-lg bg-surface text-[12px] font-semibold text-ink-2"
          >
            {initials(organization.name)}
          </span>
        ) : (
          <span className="truncate text-base font-semibold">{organization.name}</span>
        )}
      </div>

      {/* Đổi workspace cần menu rộng — chỉ hiện khi sidebar mở. */}
      {collapsed ? null : <WorkspaceSwitcher />}

      <nav className={`flex flex-col gap-1 py-3 ${collapsed ? "px-2" : "px-3"}`}>
        {nav.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={collapsed ? item.label : undefined}
              aria-label={collapsed ? item.label : undefined}
              className={`flex items-center rounded-md text-[15px] transition-colors ${
                collapsed ? "justify-center px-0 py-2.5" : "gap-3 px-3 py-2.5"
              } ${
                active
                  ? "border border-line bg-surface font-medium text-ink"
                  : "border border-transparent text-ink-2 hover:bg-[#eaeae7]"
              }`}
            >
              <span className={active ? "text-accent" : "text-ink-3"}>{item.icon}</span>
              {collapsed ? null : <span className="grow">{item.label}</span>}
            </Link>
          );
        })}
      </nav>

      <div className="grow" />

      <button
        type="button"
        onClick={onToggleCollapse}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className={`m-2 flex items-center rounded-md border border-transparent py-2 text-[13px] text-ink-3 transition-colors hover:bg-[#eaeae7] ${
          collapsed ? "justify-center px-0" : "gap-2.5 px-3"
        }`}
      >
        <IconCollapse collapsed={collapsed} />
        {collapsed ? null : <span>Collapse</span>}
      </button>
    </>
  );
}

/**
 * Mục lục Settings.
 *
 * Chỉ Workspaces là đường thật; những mục còn lại vẽ mờ và không bấm được, vì
 * chúng chưa có màn nào. Một link dẫn tới trang trắng tệ hơn một mục xám nói
 * thẳng là chưa tới lượt.
 */
function SettingsSidebar() {
  const pathname = usePathname();

  const sections: { label: string; items: { label: string; href?: string }[] }[] = [
    {
      label: "Organisation",
      items: [
        { label: "General" },
        { label: "Members", href: "/settings/members" },
        { label: "Workspaces", href: "/settings/workspaces" },
        { label: "Connections", href: "/settings/connections" },
        { label: "Audit log" },
      ],
    },
    { label: "Billing", items: [{ label: "Usage" }, { label: "Plan & invoices" }] },
  ];

  return (
    <>
      <Link
        href="/agent"
        className="flex h-16 shrink-0 items-center gap-2.5 border-b border-[#e9e9e6] px-4 text-ink-3 hover:text-ink"
      >
        <IconBack />
        <span className="text-base font-semibold text-ink">Settings</span>
      </Link>

      {sections.map((section) => (
        <div key={section.label} className="flex flex-col gap-1 px-3 pt-3">
          <span className="px-3 pb-1 text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
            {section.label}
          </span>
          {section.items.map((item) =>
            item.href ? (
              <Link
                key={item.label}
                href={item.href}
                className={`rounded-md px-3 py-2 text-sm transition-colors ${
                  pathname.startsWith(item.href)
                    ? "border border-line bg-surface font-medium text-ink"
                    : "border border-transparent text-ink-2 hover:bg-[#eaeae7]"
                }`}
              >
                {item.label}
              </Link>
            ) : (
              <span
                key={item.label}
                title="Not built yet"
                className="cursor-default rounded-md border border-transparent px-3 py-2 text-sm text-ink-4"
              >
                {item.label}
              </span>
            ),
          )}
        </div>
      ))}
    </>
  );
}

// ─── Icon, vẽ inline theo lưới 16px ─────────────────────────────────────────

const stroke = {
  width: 18,
  height: 18,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const IconChat = () => (
  <svg {...stroke} aria-hidden>
    <path d="M13.5 9.5a2 2 0 01-2 2H6l-3.5 2.5V4.5a2 2 0 012-2h7a2 2 0 012 2z" />
  </svg>
);

const IconNodes = () => (
  <svg {...stroke} aria-hidden>
    <circle cx="4" cy="4" r="2" />
    <circle cx="12" cy="4" r="2" />
    <circle cx="8" cy="12" r="2" />
    <path d="M5.4 5.6L7.2 10.2M10.6 5.6L8.8 10.2" />
  </svg>
);

const IconRun = () => (
  <svg {...stroke} aria-hidden>
    <circle cx="8" cy="8" r="6" />
    <path d="M6.6 5.6l4 2.4-4 2.4z" />
  </svg>
);

const IconPlug = () => (
  <svg {...stroke} aria-hidden>
    <path d="M6 2v3.5M10 2v3.5M4.5 5.5h7v2.2a3.5 3.5 0 01-7 0zM8 11.2V14" />
  </svg>
);

const IconGear = () => (
  <svg {...stroke} aria-hidden>
    <circle cx="8" cy="8" r="2.4" />
    <path d="M8 1.6v1.8M8 12.6v1.8M1.6 8h1.8M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3" />
  </svg>
);

const IconPlus = () => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    aria-hidden
  >
    <path d="M8 3v10M3 8h10" />
  </svg>
);

const IconCollapse = ({ collapsed }: { collapsed: boolean }) => (
  <svg {...stroke} aria-hidden>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M6 3v10" />
    {collapsed ? <path d="M9.5 6.5L11 8l-1.5 1.5" /> : <path d="M11 6.5L9.5 8l1.5 1.5" />}
  </svg>
);

const IconBack = () => (
  <svg
    width="15"
    height="15"
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    <path d="M10 3L5 8l5 5" />
  </svg>
);
