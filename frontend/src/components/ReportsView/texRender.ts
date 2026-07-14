import { unified } from "unified";
import rehypeStringify from "rehype-stringify";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { Schema } from "hast-util-sanitize";
import { unifiedLatexToHast } from "@unified-latex/unified-latex-to-hast";
import { unifiedLatexFromString } from "@unified-latex/unified-latex-util-parse";

/** XSS hardening (a cloned/untrusted graph's report is attacker-controlled input rendered
 * in-origin via dangerouslySetInnerHTML in ReportTexView): unified-latex-to-hast builds `\href`
 * / `\url` / `\hyperref` straight into `<a href=...>` and `\includegraphics` into `<img
 * src=...>` with NO scheme check at all -- `\href{javascript:alert(1)}{click}` round-trips
 * unchanged. rehype-sanitize's default schema already restricts `href`/`src` protocols, but
 * ALSO strips `className`/`style`/`data-*` from every element by default, which would silently
 * break this pipeline's own output: ReportTexView's KaTeX pass selects math spans by
 * `.inline-math`/`.display-math` (losing className there isn't a style regression, it's math
 * silently never rendering), and `textcolor`/`makebox` rely on inline `style`, `vspace`/
 * `hspace` on `data-amount`. Extending the default schema (not writing one from scratch) keeps
 * every other default protection (script stripping, all the other attribute/tag limits)
 * intact; only protocols and the extra attributes this specific renderer's own macro->hast
 * mapping (texRender's macros above) is known to emit are added back. */
const REPORT_TEX_SANITIZE_SCHEMA: Schema = {
  ...defaultSchema,
  protocols: {
    ...defaultSchema.protocols,
    href: ["http", "https", "mailto"], // no javascript:/data:/vbscript:/etc
    src: ["http", "https"],
  },
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "className", "style", "dataAmount"],
  },
};

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
 * rehypeSanitize runs between unifiedLatexToHast and rehypeStringify -- sanitizing the hast
 * tree itself, before it's ever serialized to a string, so there is no window where unsafe
 * HTML exists as a string this function could return.
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
    .use(rehypeSanitize, REPORT_TEX_SANITIZE_SCHEMA)
    .use(rehypeStringify)
    .processSync(source)
    .value as string;
}
