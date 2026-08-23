"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Button, Dialog, ErrorNote } from "@/components/ui";
import { ApiError, type OrgRole } from "@/lib/api";
import { useAddMember } from "@/lib/queries";
import { canAssignOrgRole, ORG_ROLES } from "@/lib/roles";
import { useTenant } from "@/lib/tenant";

/**
 * Thêm một người đã có tài khoản vào organization.
 *
 * Đây không phải lời mời, và hộp thoại nói thẳng điều đó ngay dòng đầu — không
 * có thư nào được gửi đi, người kia phải tự đăng ký trước. Nói trước rẻ hơn để
 * người dùng phát hiện sau khi bấm.
 *
 * Cũng không có bước tra email trước khi gửi: biết một email đã có tài khoản
 * hay chưa chính là biết ai đã đăng ký, nên backend không mở đường nào để hỏi
 * (xem `test_khong_du_quyen_thi_khong_do_duoc_email_nao_ton_tai`). Câu trả lời
 * chỉ tới sau khi thêm, và `NoAccountNote` là chỗ hứng nó.
 */
export function AddMemberDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (userId: string) => void;
}) {
  const { organization } = useTenant();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrgRole>("member");
  const [error, setError] = useState<unknown>(null);

  const add = useAddMember(organization.id);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !add.isPending) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, add.isPending]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const member = await add.mutateAsync({ email: email.trim(), role });
      onAdded(member.user_id);
    } catch (caught) {
      setError(caught);
    }
  }

  // Backend gắn `reason` vào `details` đúng để chỗ này nhận ra ngõ cụt hay gặp
  // nhất mà không phải so khớp nội dung thông điệp.
  const reason = error instanceof ApiError ? error.details.reason : undefined;
  const noAccount = reason === "no_account";
  const alreadyMember = reason === "already_member";

  return (
    <Dialog
      labelledBy="add-member-title"
      onDismiss={add.isPending ? undefined : onClose}
      className="max-w-[500px]"
    >
      <form onSubmit={submit} className="flex flex-col gap-6 p-7">
        <div className="flex flex-col gap-1.5">
          <h2 id="add-member-title" className="text-xl font-semibold tracking-tight">
            Add member
          </h2>
          <p className="text-sm leading-relaxed text-ink-3">
            Gives someone who already has an AgentWorkflow account a seat in {organization.name}.
            Nothing is emailed — {organization.name} is simply there the next time they sign in.
          </p>
        </div>

        <div className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Email</span>
            <input
              required
              autoFocus
              type="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                // Thông điệp cũ nói về địa chỉ cũ. Gõ tiếp là nó hết đúng.
                if (error) setError(null);
              }}
              placeholder="giang@acme.com"
              aria-invalid={noAccount || alreadyMember}
              className={`h-11 rounded-control border bg-surface px-3.5 text-base outline-none transition-colors placeholder:text-ink-4 ${
                noAccount || alreadyMember
                  ? "border-[1.5px] border-danger-line"
                  : "border-line-strong focus:border-accent"
              }`}
            />
            {!noAccount && !alreadyMember ? (
              <span className="text-[13px] leading-relaxed text-ink-4">
                The address on their account, not necessarily their work address.
              </span>
            ) : null}
          </label>

          {noAccount ? <NoAccountNote email={email.trim()} /> : null}
          {alreadyMember ? (
            <p className="rounded-md border border-line bg-canvas px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
              That account is already in {organization.name}. You can change what they can reach
              from their row in the list.
            </p>
          ) : null}

          <fieldset className="flex flex-col gap-1.5">
            <legend className="pb-1.5 text-sm font-medium text-ink-2">Org role</legend>
            <div className="flex flex-col gap-1.5">
              {ORG_ROLES.map((option) => {
                // Chỉ OWNER mới cấp được OWNER. Ẩn đi thì người dùng tưởng
                // không có; để đó và khoá lại thì họ biết nó tồn tại và vì sao
                // mình không dùng được.
                const allowed = canAssignOrgRole({
                  actorRole: organization.role,
                  targetCurrentRole: null,
                  newRole: option.value,
                });
                const checked = role === option.value;
                return (
                  <label
                    key={option.value}
                    className={`flex items-start gap-3 rounded-control border px-3.5 py-3 transition-colors ${
                      checked
                        ? "border-accent bg-accent-wash"
                        : allowed
                          ? "border-line-strong bg-surface hover:bg-canvas"
                          : "border-line bg-canvas"
                    } ${allowed ? "cursor-pointer" : "cursor-not-allowed"}`}
                  >
                    <input
                      type="radio"
                      name="org-role"
                      value={option.value}
                      checked={checked}
                      disabled={!allowed}
                      onChange={() => setRole(option.value)}
                      className="mt-1 accent-accent"
                    />
                    <span className="flex flex-col gap-0.5">
                      <span
                        className={`text-sm font-medium ${allowed ? "text-ink" : "text-ink-4"}`}
                      >
                        {option.label}
                      </span>
                      <span className="text-[13px] leading-relaxed text-ink-4">
                        {allowed
                          ? option.description
                          : "Only an owner can hand out the Owner role."}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <p className="rounded-md border border-line bg-canvas px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
            A new member lands with no workspace at all. You grant those from the member panel once
            they are in.
          </p>
        </div>

        {/* Hai lỗi trên đã có chỗ riêng; phần còn lại rơi vào đây. */}
        {noAccount || alreadyMember ? null : <ErrorNote error={error} />}

        <div className="flex items-center justify-end gap-2.5">
          <Button type="button" variant="secondary" onClick={onClose} disabled={add.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={add.isPending || email.trim().length === 0}>
            {add.isPending ? "Adding…" : "Add member"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Ngõ cụt: email không khớp tài khoản nào.
 *
 * Không có gì để gửi từ đây, nên thứ duy nhất hữu ích là đường link để người
 * dùng tự chuyển cho người kia — chép được, chứ không phải đọc rồi gõ lại.
 */
function NoAccountNote({ email }: { email: string }) {
  const [copied, setCopied] = useState(false);
  // Link tuyệt đối để dán vào chat hay email nào cũng còn dùng được.
  const signUpUrl =
    typeof window === "undefined" ? "/sign-up" : `${window.location.origin}/sign-up`;

  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-danger-line bg-danger-wash px-3.5 py-3">
      <span className="text-sm leading-relaxed font-medium text-danger">
        No AgentWorkflow account uses {email}.
      </span>
      <span className="text-[13px] leading-relaxed text-ink-2">
        People join by signing up themselves — there is no invitation to send from here. Give them
        the link, then add them once the account exists.
      </span>
      <div className="flex items-center gap-2 rounded-control border border-danger-line bg-surface px-3 py-2">
        <span className="min-w-0 grow truncate font-mono text-[13px] text-ink-3">{signUpUrl}</span>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(signUpUrl);
              setCopied(true);
            } catch {
              // Trình duyệt từ chối clipboard thì link vẫn nằm đó để bôi đen.
              setCopied(false);
            }
          }}
          className="shrink-0 text-[13px] font-medium text-accent hover:text-accent-ink"
        >
          {copied ? "Copied" : "Copy link"}
        </button>
      </div>
    </div>
  );
}
