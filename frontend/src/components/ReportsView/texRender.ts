import { unified } from "unified";
import rehypeStringify from "rehype-stringify";
import { unifiedLatexToHast } from "@unified-latex/unified-latex-to-hast";
import { unifiedLatexFromString } from "@unified-latex/unified-latex-util-parse";

/** .tex source -> HTML string (structure only -- \section/\subsection, itemize/enumerate,
 * tabular, textbf/textit, etc). unified-latex-to-hast does NOT evaluate \(...\)/$...$ math
 * itself; it emits raw LaTeX source inside <span class="inline-math"> / <div
 * class="display-math">, which ReportTexView's own effect fills in with KaTeX client-side
 * after this HTML is mounted. Verified (Phase 0, this feature) against latex.js first --
 * latex.js has no \tabular support at all (two long-open upstream issues, #113 and #162) and
 * was rejected; unified-latex is the "unified/remark-latex equivalent" fallback the brief
 * authorized, and it round-trips tabular, sections, lists, and bold/emph cleanly with zero
 * network dependency (pure string->string, no CDN/font fetches).
 *
 * Throws only on a genuinely fatal parse error -- empirically the library is very permissive
 * and degrades malformed/unknown input into a best-effort rendering (unterminated
 * environments, unknown macros/environments) rather than throwing. Callers must still catch:
 * this is the one path that can turn a bad .tex file into an exception instead of odd-looking
 * HTML, and the product requirement is "never a blank pane or crash" -- show the error and the
 * raw source instead. */
export function renderLatexToHtml(source: string): string {
  return unified()
    .use(unifiedLatexFromString)
    .use(unifiedLatexToHast)
    .use(rehypeStringify)
    .processSync(source)
    .value as string;
}
