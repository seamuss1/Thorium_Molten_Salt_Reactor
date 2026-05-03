import { type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode, useState } from "react";

interface ExpandableTextProps {
  children: ReactNode;
  className?: string;
  insideInteractive?: boolean;
  lines?: number;
  title?: string;
}

export function ExpandableText({ children, className, insideInteractive = false, lines = 1, title }: ExpandableTextProps) {
  const [expanded, setExpanded] = useState(false);
  const textTitle = title ?? (typeof children === "string" || typeof children === "number" ? String(children) : undefined);
  const style = { "--line-count": String(lines) } as CSSProperties;
  const clampStyle = { WebkitLineClamp: expanded ? undefined : lines } as CSSProperties;

  function toggle(event: MouseEvent<HTMLSpanElement>) {
    if (!insideInteractive) {
      event.preventDefault();
      event.stopPropagation();
    }
    setExpanded((current) => !current);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setExpanded((current) => !current);
  }

  return (
    <span
      aria-expanded={insideInteractive ? undefined : expanded}
      className={["expandable-text", expanded ? "expanded" : "", className].filter(Boolean).join(" ")}
      onClick={toggle}
      onKeyDown={insideInteractive ? undefined : handleKeyDown}
      role={insideInteractive ? undefined : "button"}
      style={style}
      tabIndex={insideInteractive ? undefined : 0}
      title={textTitle}
    >
      <span style={clampStyle}>{children}</span>
    </span>
  );
}
