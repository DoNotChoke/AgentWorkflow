"use client";

import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Vẽ câu trả lời của Agent dưới dạng Markdown (GFM: bảng, gạch ngang, tick).
 *
 * `react-markdown` mặc định KHÔNG render HTML thô (không nạp `rehype-raw`), nên
 * chuỗi model trả về được coi là markdown thuần — không có khe chèn thẻ.
 *
 * Mỗi phần tử map về token trong `globals.css` để khối văn bản này hoà vào
 * bong bóng chat (`text-sm leading-relaxed`) thay vì mang style mặc định của
 * trình duyệt.
 */

const components: Components = {
  p: (props) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  h1: (props) => <h1 className="mt-4 mb-2 text-[15px] font-semibold first:mt-0" {...props} />,
  h2: (props) => <h2 className="mt-4 mb-2 text-[14px] font-semibold first:mt-0" {...props} />,
  h3: (props) => <h3 className="mt-3 mb-1.5 text-[13px] font-semibold first:mt-0" {...props} />,
  h4: (props) => <h4 className="mt-3 mb-1.5 text-[13px] font-semibold first:mt-0" {...props} />,
  ul: (props) => <ul className="my-2 list-disc space-y-1 pl-5 first:mt-0 last:mb-0" {...props} />,
  ol: (props) => <ol className="my-2 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0" {...props} />,
  li: (props) => <li className="pl-0.5 leading-relaxed" {...props} />,
  a: (props) => (
    <a
      className="text-accent underline underline-offset-2 hover:text-accent-ink"
      target="_blank"
      rel="noreferrer noopener"
      {...props}
    />
  ),
  strong: (props) => <strong className="font-semibold" {...props} />,
  em: (props) => <em className="italic" {...props} />,
  hr: () => <hr className="my-3 border-line" />,
  blockquote: (props) => (
    <blockquote
      className="my-2 border-l-2 border-line-strong pl-3 text-ink-3 first:mt-0 last:mb-0"
      {...props}
    />
  ),
  code: ({ className, children, ...props }: ComponentPropsWithoutRef<"code">) => {
    // Trong markdown, code khối được bọc trong <pre><code>; code inline thì không.
    // `react-markdown` không đánh dấu inline nữa (v9+), nên phân biệt bằng việc
    // có class ngôn ngữ hay có xuống dòng hay không.
    const isBlock = /language-/.test(className ?? "") || String(children).includes("\n");
    if (isBlock) {
      return (
        <code className={`font-mono text-[12.5px] ${className ?? ""}`} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code
        className="rounded border border-line bg-sunken px-1 py-0.5 font-mono text-[12.5px]"
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: (props) => (
    <pre
      className="my-2 overflow-x-auto rounded-lg border border-line bg-canvas p-3 first:mt-0 last:mb-0"
      {...props}
    />
  ),
  table: (props) => (
    <div className="my-2 overflow-x-auto first:mt-0 last:mb-0">
      <table className="w-full border-collapse text-[13px]" {...props} />
    </div>
  ),
  thead: (props) => <thead className="border-b border-line-strong" {...props} />,
  th: (props) => <th className="px-2.5 py-1.5 text-left font-semibold" {...props} />,
  td: (props) => <td className="border-b border-line px-2.5 py-1.5 align-top" {...props} />,
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
