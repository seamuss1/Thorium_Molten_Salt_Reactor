import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, ListTree, Sigma } from "lucide-react";
import { api } from "../api";
import { Markdown } from "../components/Markdown";

const formulaCards = [
  {
    label: "Loop energy",
    expression: "$$Q = \\dot m c_p \\Delta T$$"
  },
  {
    label: "Hydraulic budget",
    expression: "$$\\Delta p = f\\frac{L}{D}\\frac{\\rho u^2}{2}+\\sum_j K_j\\frac{\\rho u^2}{2}+\\rho g\\Delta z$$"
  },
  {
    label: "Precursor transport",
    expression: "$$\\frac{dC_i}{dt}+\\nabla\\cdot(uC_i)=\\nabla\\cdot(D_i\\nabla C_i)+S_{i,f}-\\lambda_i C_i$$"
  }
];

export function Docs() {
  const params = useParams();
  const docs = useQuery({ queryKey: ["docs"], queryFn: api.docs });
  const slug = params.slug ?? docs.data?.[0]?.slug;
  const doc = useQuery({ queryKey: ["doc", slug], queryFn: () => api.doc(slug!), enabled: Boolean(slug) });
  const headings = useMemo(() => doc.data?.headings.slice(1, 9) ?? [], [doc.data]);
  const docCount = docs.data?.length ?? 0;

  return (
    <div className="page split-page science-page">
      <section className="list-panel science-index">
        <div className="section-title">
          <BookOpen aria-hidden="true" />
          <div>
            <h1>Science</h1>
            <span>{docCount} living notes</span>
          </div>
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
            <header className="page-header compact science-header">
              <div>
                <p className="eyebrow">{doc.data.path}</p>
                <h1>{doc.data.title}</h1>
                <div className="science-meta">
                  <span>{doc.data.headings.length} sections</span>
                  <span>Repository note</span>
                  <span>Equation notation</span>
                </div>
              </div>
            </header>
            <div className="docs-layout">
              <Markdown className="markdown-body science-article" content={doc.data.content} />
              <aside className="science-rail">
                <section className="toc">
                  <div className="section-title">
                    <ListTree aria-hidden="true" />
                    <h2>Outline</h2>
                  </div>
                  {headings.map((heading) => (
                    <span key={heading}>{heading}</span>
                  ))}
                </section>
                <section className="formula-stack">
                  <div className="section-title">
                    <Sigma aria-hidden="true" />
                    <h2>Core Relations</h2>
                  </div>
                  {formulaCards.map((card) => (
                    <div className="formula-card" key={card.label}>
                      <span>{card.label}</span>
                      <Markdown className="formula-math" content={card.expression} />
                    </div>
                  ))}
                </section>
              </aside>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
