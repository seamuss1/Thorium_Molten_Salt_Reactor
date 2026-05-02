import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

interface MarkdownProps {
  className?: string;
  content: string;
}

export function Markdown({ className = "markdown-body", content }: MarkdownProps) {
  return (
    <ReactMarkdown className={className} remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
      {content}
    </ReactMarkdown>
  );
}
