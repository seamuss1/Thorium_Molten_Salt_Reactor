import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { BookOpen, ListTree } from "lucide-react";
import { api } from "../api";

export function Docs() {
  const params = useParams();
  const docs = useQuery({ queryKey: ["docs"], queryFn: api.docs });
  const slug = params.slug ?? docs.data?.[0]?.slug;
  const doc = useQuery({ queryKey: ["doc", slug], queryFn: () => api.doc(slug!), enabled: Boolean(slug) });
  const headings = useMemo(() => doc.data?.headings.slice(1, 9) ?? [], [doc.data]);

  return (
    <div className="page split-page">
      <section className="list-panel">
        <div className="section-title">
          <BookOpen aria-hidden="true" />
          <h1>Science</h1>
        </div>
        <div className="doc-links">
          {docs.data?.map((item) => (
            <Link key={item.slug} className={item.slug === slug ? "selected" : ""} to={`/docs/${item.slug}`}>
              <strong>{item.title}</strong>
              <small>{item.path}</small>
            </Link>
          ))}
        </div>
      </section>
      <section className="detail-panel docs-panel">
        {doc.data && (
          <>
            <header className="page-header compact">
              <div>
                <p className="eyebrow">{doc.data.path}</p>
                <h1>{doc.data.title}</h1>
              </div>
            </header>
            <div className="docs-layout">
              <article className="markdown-body">
                <ReactMarkdown>{doc.data.content}</ReactMarkdown>
              </article>
              <aside className="toc">
                <div className="section-title">
                  <ListTree aria-hidden="true" />
                  <h2>Headings</h2>
                </div>
                {headings.map((heading) => (
                  <span key={heading}>{heading}</span>
                ))}
              </aside>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
