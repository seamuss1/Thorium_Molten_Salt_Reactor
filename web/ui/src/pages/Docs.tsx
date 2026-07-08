import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, ListTree } from "lucide-react";
import { api } from "../api";
import { Truncate } from "../components/Truncate";
import { PanelError, PanelLoading, EmptyState } from "../components/StateBlock";
import { Markdown, makeSlugger } from "../components/Markdown";

export function Docs() {
  const params = useParams();
  const docs = useQuery({ queryKey: ["docs"], queryFn: api.docs });
  const slug = params.slug ?? docs.data?.[0]?.slug;
  const doc = useQuery({ queryKey: ["doc", slug], queryFn: () => api.doc(slug!), enabled: Boolean(slug) });
  const outline = useMemo(() => {
    if (!doc.data) return [];
    // Slug the full heading list in order (same algorithm the renderer uses) so
    // ids line up, including de-duplication of repeated heading text; the first
    // heading is the page title, shown in the header rather than the outline.
    const slug = makeSlugger();
    return doc.data.headings.map((heading) => ({ text: heading, id: slug(heading) })).slice(1, 12);
  }, [doc.data]);
  const readMeta = useMemo(() => {
    if (!doc.data) return null;
    const words = doc.data.content.trim().split(/\s+/).filter(Boolean).length;
    return { words, minutes: Math.max(1, Math.round(words / 200)) };
  }, [doc.data]);
  const docCount = docs.data?.length ?? 0;

  return (
    <div className="page split-page science-page">
      <section className="list-panel science-index">
        <div className="section-title">
          <BookOpen aria-hidden="true" />
          <div>
            <h1>Science</h1>
            <span>{docs.isLoading ? "Loading…" : `${docCount} reference notes`}</span>
          </div>
        </div>
        {docs.isLoading ? (
          <PanelLoading label="Loading documents" lines={6} />
        ) : docs.isError ? (
          <PanelError error={docs.error} onRetry={() => docs.refetch()} />
        ) : docs.data?.length ? (
          <div className="doc-links">
            {docs.data.map((item) => {
              const isSelected = item.slug === slug;
              return (
                <Link key={item.slug} className={isSelected ? "selected" : ""} aria-current={isSelected ? "page" : undefined} to={`/docs/${item.slug}`}>
                  <Truncate className="list-title">{item.title}</Truncate>
                  <Truncate className="list-meta">{item.path}</Truncate>
                </Link>
              );
            })}
          </div>
        ) : (
          <EmptyState icon={BookOpen}>No documents found.</EmptyState>
        )}
      </section>
      <section className="detail-panel docs-panel">
        {doc.isLoading ? (
          <PanelLoading label="Loading document" lines={10} tall />
        ) : doc.isError ? (
          <PanelError error={doc.error} onRetry={() => doc.refetch()} tall />
        ) : doc.data ? (
          <>
            <header className="page-header compact science-header">
              <div>
                <p className="eyebrow">
                  <Truncate>{doc.data.path}</Truncate>
                </p>
                <h1>
                  <Truncate lines={2}>{doc.data.title}</Truncate>
                </h1>
                <div className="science-meta">
                  <span>{doc.data.headings.length} sections</span>
                  {readMeta && <span>{readMeta.words.toLocaleString()} words</span>}
                  {readMeta && <span>{readMeta.minutes} min read</span>}
                </div>
              </div>
            </header>
            <div className="docs-layout">
              <Markdown className="markdown-body science-article" content={doc.data.content} anchors />
              {outline.length > 0 && (
                <aside className="science-rail">
                  <nav className="toc" aria-label="Document outline">
                    <div className="section-title">
                      <ListTree aria-hidden="true" />
                      <h2>Outline</h2>
                    </div>
                    {outline.map((heading) => (
                      <a
                        key={heading.id}
                        className="toc-item"
                        href={`#${heading.id}`}
                        onClick={(event) => {
                          const target = document.getElementById(heading.id);
                          if (target) {
                            event.preventDefault();
                            target.scrollIntoView({ behavior: "smooth", block: "start" });
                          }
                        }}
                      >
                        {heading.text}
                      </a>
                    ))}
                  </nav>
                </aside>
              )}
            </div>
          </>
        ) : (
          <EmptyState tall icon={BookOpen}>Select a document to read.</EmptyState>
        )}
      </section>
    </div>
  );
}
