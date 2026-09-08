/**
 * Danh mục model cho màn hình tạo Agent.
 *
 * Chép tay từ `MODEL_CATALOG` ở backend (`agent/infrastructure/model_catalog.py`)
 * — danh mục là cấu hình triển khai, đổi rất hiếm, và backend chưa mở endpoint
 * nào để đọc nó. Nếu một model bị bỏ ở backend, `run_turn` sẽ báo
 * `UnknownModelError` rõ ràng qua sự kiện SSE `error`, không phải hỏng âm thầm.
 *
 * Một provider có thể chưa được cấu hình API key ở lần triển khai này — lúc đó
 * lượt chat đầu tiên báo `UnconfiguredProviderError`. Không lọc trước ở đây vì
 * frontend không biết key nào đã đặt.
 */

export type ModelOption = {
  id: string;
  label: string;
  family: "anthropic" | "openai";
  blurb: string;
};

export const MODEL_OPTIONS: ModelOption[] = [
  {
    id: "claude-opus-5",
    label: "Claude Opus 5",
    family: "anthropic",
    blurb: "Most capable — best for judgment-heavy or ambiguous work.",
  },
  {
    id: "claude-sonnet-5",
    label: "Claude Sonnet 5",
    family: "anthropic",
    blurb: "Balanced default — fast enough for most agents.",
  },
  {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    family: "anthropic",
    blurb: "Fastest and cheapest — good for narrow, repetitive tasks.",
  },
  {
    id: "gpt-5.6-sol",
    label: "GPT-5.6 Sol",
    family: "openai",
    blurb: "Newest OpenAI flagship — strongest reasoning, Opus-tier.",
  },
  {
    id: "gpt-5.6-terra",
    label: "GPT-5.6 Terra",
    family: "openai",
    blurb: "Newest OpenAI mid-tier — fast and cheaper, Sonnet-tier.",
  },
  {
    id: "gpt-5.4",
    label: "GPT-5.4",
    family: "openai",
    blurb: "OpenAI alternative, roughly Sonnet-tier.",
  },
  {
    id: "gpt-5.4-mini",
    label: "GPT-5.4 mini",
    family: "openai",
    blurb: "OpenAI alternative, roughly Haiku-tier.",
  },
];

/** Mặc định khi mở form — khớp `anthropic_default_model` của backend. */
export const DEFAULT_MODEL_ID = "claude-sonnet-5";

export function modelLabel(id: string): string {
  return MODEL_OPTIONS.find((m) => m.id === id)?.label ?? id;
}
