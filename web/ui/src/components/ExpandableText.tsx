import {
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState
} from "react";

interface ExpandableTextProps {
  children: ReactNode;
  className?: string;
  insideInteractive?: boolean;
  lines?: number;
  title?: string;
}

/**
 * Click-to-expand for genuinely long free text (summaries, notes, messages).
 * The expand affordance (button role, tab stop, aria-expanded) is exposed only
 * when the content actually overflows its clamp, so short bounded values don't
 * emit spurious keyboard/screen-reader stops.
 */
export function ExpandableText({ children, className, insideInteractive = false, lines = 1, title }: ExpandableTextProps) {
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);
  const innerRef = useRef<HTMLSpanElement>(null);
  const textTitle = title ?? (typeof children === "string" || typeof children === "number" ? String(children) : undefined);
  const style = { "--line-count": String(lines) } as CSSProperties;
  const clampStyle = { WebkitLineClamp: expanded ? undefined : lines } as CSSProperties;

  const measure = useCallback(() => {
    const node = innerRef.current;
    if (!node) return;
    setClamped(node.scrollHeight - 1 > node.clientHeight || node.scrollWidth - 1 > node.clientWidth);
  }, []);

  useLayoutEffect(() => {
    if (expanded) return;
    measure();
  }, [children, lines, expanded, measure]);

  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return;
    const node = innerRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => {
      if (!expanded) measure();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [expanded, measure]);

  // Interactive only when the parent isn't already interactive and the text is
  // (or was) actually clamped.
  const interactive = !insideInteractive && (clamped || expanded);

  function toggle(event: MouseEvent<HTMLSpanElement>) {
    if (!interactive) return;
    event.preventDefault();
    event.stopPropagation();
    setExpanded((current) => !current);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setExpanded((current) => !current);
  }

  return (
    <span
      aria-expanded={interactive ? expanded : undefined}
      className={["expandable-text", interactive ? "interactive" : "", expanded ? "expanded" : "", className].filter(Boolean).join(" ")}
      onClick={interactive ? toggle : undefined}
      onKeyDown={interactive ? handleKeyDown : undefined}
      role={interactive ? "button" : undefined}
      style={style}
      tabIndex={interactive ? 0 : undefined}
      title={textTitle}
    >
      <span ref={innerRef} style={clampStyle}>
        {children}
      </span>
    </span>
  );
}
