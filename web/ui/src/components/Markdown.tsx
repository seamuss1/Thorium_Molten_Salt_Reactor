import type { ComponentType, ReactNode } from "react";
import { isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

interface MarkdownProps {
  className?: string;
  content: string;
  /** Add id anchors to headings so an outline can link to them. */
  anchors?: boolean;
}

/** GitHub-style slug for a single heading (before de-duplication). */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
}

/**
 * A stateful slugger that disambiguates repeated headings the way GitHub does
 * (`results`, `results-1`, …). The Docs outline and the rendered headings each
 * instantiate one over the same ordered heading list, so their ids stay in sync.
 */
export function makeSlugger(): (text: string) => string {
  const seen = new Map<string, number>();
  return (text: string) => {
    const base = slugify(text);
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    return count === 0 ? base : `${base}-${count}`;
  };
}

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

const HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;

/** Fresh heading renderers per call so the slugger counter resets each render. */
function buildHeadingComponents() {
  const slug = makeSlugger();
  const components: Record<string, ComponentType<{ children?: ReactNode }>> = {};
  for (const tag of HEADING_TAGS) {
    const Tag = tag;
    components[tag] = ({ children }) => <Tag id={slug(textOf(children))}>{children}</Tag>;
  }
  return components;
}

export function Markdown({ className = "markdown-body", content, anchors = false }: MarkdownProps) {
  // Rebuilt each render (cheap) so heading de-duplication is deterministic and
  // never accumulates counts across renders.
  const components = anchors ? buildHeadingComponents() : undefined;
  return (
    <ReactMarkdown className={className} remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]} components={components}>
      {content}
    </ReactMarkdown>
  );
}
