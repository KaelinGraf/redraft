---
name: redraft-fetch-docs
description: Re-fetch a redraft graph's local docs/ cache from the source_url recorded on each artifact node. Use after a fresh clone (docs/ starts out empty), when a referenced doc_path is missing locally, or when the user asks to restore/re-download saved reference material.
allowed-tools:
  - Bash(grep *)
  - Bash(curl *)
  - Bash(mkdir *)
  - Bash(ls *)
---

`docs/` is a local, gitignored cache (organizing protocol §2) — it never travels with a
clone or `redraft sync`. This skill restores it from the `source_url` recorded on each
`artifact` node, entirely with `grep`/`curl`/`mkdir`/`ls` — no MCP server required.

Follow these steps in order. Do not skip ahead or combine steps.

1. **Find the graph root.** Check whether `graph/nodes/` exists at the project root /
   current working directory — the same detection `redraft-init` step 1 uses. If it
   doesn't, this isn't a graph repo; stop and say so.

2. **Scan for fetchable artifacts.** Run:

   ```
   grep -nE "doc_path:|source_url:" graph/nodes/*.md
   ```

   If that reports "No such file or directory," `graph/nodes/` has no node files yet —
   there's nothing to fetch; report 0 fetched / 0 already-present / 0 failed and stop.

   Otherwise each output line is `<file>:<line>:<content>`. Group lines by `<file>`: a
   node only qualifies when **both** `doc_path:` and `source_url:` appear for it (that
   pairing is the artifact-node convention from organizing protocol §2 — a file with
   only one of the two is a bare citation or a node mid-edit, not this skill's job).
   Read the value after the colon on each qualifying line — strip a wrapping `"` or `'`
   if the YAML dumper added one — to get that node's `doc_path` and `source_url`.

3. **Resolve each qualifying pair, one at a time.** Quote paths and URLs in every
   command below (`doc_path` can contain spaces).
   - `ls "<graph-root>/<doc_path>"` — if it lists the file, **skip**: never re-download,
     never overwrite an existing local copy, even one you suspect is stale.
   - If `ls` reports the file missing: `mkdir -p` the **parent directory** of
     `<doc_path>` (compute it yourself from the path string, e.g. the parent of
     `docs/papers/qin-2022.pdf` is `<graph-root>/docs/papers` — don't shell out to
     `dirname`, it isn't one of this skill's allowed commands), then fetch:

     ```
     curl -L --fail --remove-on-error -o "<graph-root>/<doc_path>" "<source_url>"
     ```

     `--remove-on-error` matters: without it, a failed fetch (404, paywall, network
     error) can leave a zero-byte or truncated file sitting at `doc_path`, and a later
     run's `ls` check would then wrongly treat that corrupt stub as "already present"
     and skip it forever.

4. **Report a compact summary**, not a blow-by-blow: `fetched N, already-present M,
   failed K`. For each failure, name the node title, the `source_url`, and the error
   (curl's exit status or stderr text) — a paywalled, moved, or unreachable source is
   expected for some entries; report it and move on to the rest of the list, never stop
   on the first failure.

## What NOT to do

- Never overwrite a file that already exists at its `doc_path` (step 3).
- Never fetch a URL that isn't recorded as a `source_url` on some artifact node — this
  skill restores what's recorded, it doesn't go discover new material.
- Never touch `graph/nodes/`, and never commit or run `snapshot` — this skill only
  populates the gitignored `docs/` cache; it has no graph-write or git side effects.
- A failed fetch is reportable, not fatal — keep going through the rest of the list
  rather than aborting on the first bad URL.
