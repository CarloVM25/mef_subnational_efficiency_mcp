# Evaluator & Optimizer Skill Report

**Generated:** 2026-06-12T00:00:00 (placeholder — overwritten on first automated run)
**Target file:** app.py
**Status:** PENDING — awaiting first automated evaluator run

---

## Bugs Found & Fixed

1. [PLACEHOLDER] No automated evaluation has been run yet. Execute the evaluator skill to populate this section with real findings.
2. [PLACEHOLDER] This report is a scaffold generated at project initialization to satisfy the `AUDIT_REPORT.exists()` check in Tab 4 of the dashboard.
3. [PLACEHOLDER] All items in this section will be replaced with structured `[SEVERITY] Description — Fix applied` entries after the first evaluator run.

---

## Performance Optimizations

1. [PLACEHOLDER] `@st.cache_data` coverage audit not yet performed — will be verified and enforced on first evaluator run.
2. [PLACEHOLDER] Plotly chart empty-DataFrame guards not yet audited — unguarded `px.bar` / `px.scatter` calls will be wrapped with existence checks.
3. [PLACEHOLDER] Pipeline subprocess `cwd` and `sys.executable` usage not yet cross-checked against the actual runtime environment.

---

## Structural Changes

1. [PLACEHOLDER] CSS hover effects on metric cards (`[data-testid='stMetric']:hover`) not yet applied — scheduled for first evaluator run.
2. [PLACEHOLDER] Tab scope integrity check (Tabs 2–4 must use only 2025 data) has not been executed.
3. [PLACEHOLDER] Division-by-zero guard audit on `Avance_Pct = Devengado / PIM * 100` not yet performed.

---

## QA Verification Summary

| Check | Status | Notes |
|---|---|---|
| Aggregation cross-verification | PENDING | Requires live MCP connection to datosabiertos.gob.pe |
| `@st.cache_data` coverage | PENDING | Will scan app.py for all data-loading functions |
| Division-by-zero guards | PENDING | Will search for unguarded `/ PIM` expressions |
| Missing-file handling | PENDING | Will verify all `pd.read_csv` calls are guarded |
| Tab scope integrity | PENDING | Will confirm Tabs 2–4 contain no historical 1964 data |
| Plotly empty-DataFrame guards | PENDING | Will wrap unguarded chart calls |
| CSS / UX improvements | PENDING | Will apply Inter font, hover effects, box-shadow |

---

## Next Steps

- **Run the evaluator skill** by invoking the `evaluator_skill` Claude Code skill to replace all placeholder entries with real findings.
- **Provide a live CKAN resource ID** for the 2025 budget dataset so the cross-verification step (Section 1) can sample actual raw rows.
- **Schedule recurring evaluation** after each pipeline run to keep this report current. Consider adding a post-run hook in `.claude/settings.json` that triggers the evaluator skill automatically.
- **Review CRITICAL-severity findings** (if any) manually before merging pipeline changes to the main branch.
- **Validate OCR quality** on the 1964 PDF pages by spot-checking `data/processed/historical_1964.json` against a physical scan of the original document.
