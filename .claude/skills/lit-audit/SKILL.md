---
name: lit-audit
description: Load the literature audit coding scheme and code a paper
user-invocable: true
argument-hint: "[paper title, DOI, or URL]"
---

You are coding a paper for the TRACE literature audit (Component 1 of the paper).

1. Read `manuscript/lit_review/coding_rubric.md` for **operationalized scoring anchors** (0/1/2 definitions with examples for every variable)

2. Read `manuscript/lit_review/audit_sample.csv` to see papers already coded

3. Read `manuscript/lit_review/search_queries.md` for inclusion/exclusion criteria and domain boundary definition

4. For the paper "$ARGUMENTS":
   - Find and read the paper (use WebFetch if a URL is provided)
   - **Check ALL available materials**: main text (all sections), acknowledgments, author contributions statement, supplementary information/appendix, and linked data/code repositories (if accessible)
   - Apply all **15 variables** using the scoring anchors in `coding_rubric.md`:
     - A1 (model named), A2 (version/date), A3 (access method)
     - B1 (tasks described), B2 (human/AI delineation), B3 (iterative refinement)
     - C1 (decision attribution), C2 (alternatives considered), C3 (rationale), C4 (correction disclosure)
     - D1 (prompt disclosure), D2 (output verification), D3a (venue has AI policy), D3b (disclosure meets policy — NA if D3a=0), D4 (reproducibility artifacts)
   - Score each: 0 (absent), 1 (partial/vague), 2 (fully present). Use the boundary clarifications in the rubric to distinguish levels.
   - Compute dimension scores and overall score
   - Record metadata:
     - `peer_review_status`: published / accepted / preprint
     - `disclosure_location`: where AI disclosures were found (main_text / supplementary / acknowledgments / code_repo — can be multiple, comma-separated)
   - Add a row to `audit_sample.csv`
   - Note any interesting observations

5. If a TRACE session is active, log any gotchas or noteworthy observations
   as annotations.

**Conservative scoring rule**: When in doubt between two adjacent scores, assign the **lower** score. This prevents inflation and strengthens the finding that decision provenance is under-reported.
