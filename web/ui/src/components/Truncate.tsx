import type { CSSProperties, ReactNode } from "react";

interface TruncateProps {
  children: ReactNode;
  className?: string;
  /** Number of lines before clamping (default 1). */
  lines?: number;
  /** Native tooltip text; defaults to the string content. */
  title?: string;
  as?: "span" | "div";
}

/**
 * Bounded-value truncation: pure CSS line-clamp plus a native title tooltip.
 * Use for labels, values, counts, and metadata — anything short and known.
 * For long free text that benefits from click-to-expand, use ExpandableText.
 */
export function Truncate({ children, className, lines = 1, title, as = "span" }: TruncateProps) {
  const style = { "--clamp-lines": String(lines) } as CSSProperties;
  const tooltip = title ?? (typeof children === "string" || typeof children === "number" ? String(children) : undefined);
  const Tag = as;
  return (
    <Tag className={["truncate", className].filter(Boolean).join(" ")} style={style} title={tooltip}>
      {children}
    </Tag>
  );
}
