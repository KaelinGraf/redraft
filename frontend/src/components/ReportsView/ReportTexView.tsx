import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import katex from "katex";
import "katex/dist/katex.min.css";
import { useReport } from "../../api/queries";
import { EmptyState } from "../EmptyState";
import { renderLatexToHtml } from "./texRender";

/** CENTER, Reports tab: /reports/:filename for .tex reports (organizing-protocol.md §7 --
 * formal technical reports are authored as LaTeX, rendered here). Lazy-loaded from routes.tsx
 * (same pattern as MapView/TimelineView) since unified-latex + katex is a real bundle-size
 * cost not worth paying on every route.
 *
 * Rendered on a light "paper" card in BOTH themes (.tex-paper, index.css) rather than trying
 * to theme the compiled document body itself: unified-latex-to-hast's output is plain
 * unstyled HTML (h3/h4/p/ul/table/b/em/...) that we fully own the CSS for, so full
 * prefers-color-scheme theming was possible -- the paper-card choice here is deliberate
 * anyway, not a fallback: it reads as an intentional "this is a formatted document, distinct
 * from app chrome" metaphor (the way a PDF viewer keeps its page white regardless of the
 * surrounding app's theme), and the serif LaTeX-ish typography inside it is part of that
 * metaphor. The raw-source toggle and error path both stay outside the card, in normal
 * app-chrome styling. */
export default function ReportTexView() {
  const { filename } = useParams<{ filename: string }>();
  const { data, isPending, isError, error } = useReport(filename);
  const [showSource, setShowSource] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const { html, renderError } = useMemo(() => {
    if (!data) return { html: null as string | null, renderError: null as string | null };
    try {
      return { html: renderLatexToHtml(data.content), renderError: null };
    } catch (e) {
      return { html: null, renderError: e instanceof Error ? e.message : String(e) };
    }
  }, [data]);

  // Fill in the math spans unified-latex-to-hast left as raw source (it doesn't evaluate
  // math itself -- see texRender.ts). katex.render mutates the element in place; throwOnError:
  // false makes katex itself print an inline error span rather than throw, so the try/catch
  // here is belt-and-braces only, not the primary error path.
  useEffect(() => {
    if (!html || !containerRef.current) return;
    const mathEls = containerRef.current.querySelectorAll<HTMLElement>(".inline-math, .display-math");
    mathEls.forEach((el) => {
      const source = el.textContent ?? "";
      try {
        katex.render(source, el, { throwOnError: false, displayMode: el.classList.contains("display-math") });
      } catch {
        /* leave the raw LaTeX source text in place */
      }
    });
  }, [html]);

  if (!filename) return <EmptyState>Select a report.</EmptyState>;
  if (isPending) return <div className="spinner-line">Loading report…</div>;
  if (isError) return <EmptyState>{error instanceof Error ? error.message : "Failed to load report."}</EmptyState>;

  return (
    <div>
      <div style={{ marginBottom: 10, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Link to="/reports">← All reports</Link>
        <button className="btn btn--sm" onClick={() => setShowSource((v) => !v)}>
          {showSource ? "View rendered" : "View source"}
        </button>
      </div>
      <h2>{data.filename}</h2>

      {showSource ? (
        <div className="body-view">{data.content}</div>
      ) : renderError ? (
        <>
          <EmptyState>Failed to render this report: {renderError}</EmptyState>
          <div className="body-view">{data.content}</div>
        </>
      ) : (
        <div className="tex-paper">
          <div className="tex-render" ref={containerRef} dangerouslySetInnerHTML={{ __html: html ?? "" }} />
        </div>
      )}
    </div>
  );
}
