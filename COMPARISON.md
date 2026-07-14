# document-parser vs Wipo-agent

Smoke date: 2026-07-14. Sample PDF: `Wipo-agent/output/local_target_smoke/pdfs/WO2020061229.pdf` (~316 KB).

## Verdict

**document-parser cannot replace Wipo-agent** for the team goal (permeability / activity / sequences into a database). It is a **PDF structure-formula → SMILES** helper, overlapping Wipo’s optional `chemistry_extractor` layer—not the HTML/ST.26 target pipeline.

Wipo’s “~60 targets in 3 days” bottleneck is mainly **WIPO search / download / CAPTCHA / serial target iteration**, not HTML sequence parsing. Switching to PDF OCR would usually make throughput **worse**, not better.

## Capability matrix

| Need | document-parser | Wipo-agent |
|------|-----------------|------------|
| Target-driven patent search | No | Yes (`run_cyclic_peptide_search.py`, Playwright) |
| Bulk PDF / HTML acquisition | Input PDF only | Yes (WIPO + Google Patents text) |
| ST.26 / SEQ ID sequences | No (text may appear in MD only) | Primary path (`st26_parser`, `sequence_extractor`) |
| Sequence → SMILES (linear AA) | No | RDKit FASTA in `sequence_contexts` |
| Structure image → SMILES | **Primary** (MinerU + MolScribe) | Optional (`chemistry_extractor.py`) |
| Activity / permeability | No | Regex + LLM → SQLite |
| Persistence | Markdown files | SQLite (`patent_sequences.sqlite` / `result_store.db`) |

## Architecture

```
document-parser:
  PDF → MinerU (layout/OCR) → Markdown + images → MolScribe → *_final.md

Wipo-agent (production target path):
  Targets → WIPO search → HTML Description (prefer) / PDF VL OCR (fallback)
         → LLM structured extract → result_store.db
  Optional: ST.26 XML, RapidOCR/PaddleOCR page pipeline, MinerU+MolScribe chemistry
```

## Smoke results (this machine)

| Metric | document-parser (`pipeline` + OCR) |
|--------|-------------------------------------|
| End-to-end wall time | ~90 s (second run; models cached) |
| MinerU | OK → `output/smoke/WO2020061229/ocr/WO2020061229.md` |
| Images found | 4 (header/logo-like; not clean structure drawings) |
| MolScribe predictions | 4 completed; confidence ≤ 0.014 |
| SMILES replacements | 0 (below `--confidence 0.5`) |
| Final artifact | `output/smoke/WO2020061229_final.md` (~10 KB readable PCT front matter) |

Environment: Windows, Python 3.11 venv, RTX 3060 6GB, torch cu124. Fixes applied to run: MinerU `-m ocr` CLI, Swin `swin_base` alias, `albumentations==1.3.1`, vLLM skipped.

Wipo had the same PDF on disk but no processed OCR/fulltext artifact for this doc under `local_target_smoke` for a side-by-side text quality diff.

## Efficiency note (vs “60 targets / 3 days”)

| Layer | Cost driver | OCR-first impact |
|-------|-------------|------------------|
| Search + CAPTCHA | High (browser, rate limits) | Unchanged |
| Per-patent text | HTML Description is cheap when present | PDF OCR / MinerU **adds** minutes–hours per long patent |
| Sequences | ST.26 XML is authoritative when available | OCR sequence extraction is weaker |
| Structure SMILES | Rare; figure-dependent | document-parser / chemistry_extractor helps **here only** |

**Recommendation:** Keep optimizing Wipo (resume, less OCR, prefer ST.26/HTML, concurrency where safe). Use document-parser (or Wipo’s chemistry step) when patents have **drawn structures** and no reliable ST.26/sequence text—not as a wholesale replacement for the scrape+DB pipeline.

## Integration options (future)

1. Call document-parser as a subprocess from Wipo for selected PDFs (same dual-venv pattern as `chemistry_extractor`).
2. Or finish Wipo’s `.venv-molscribe` and keep chemistry inside Wipo; treat document-parser as a standalone lab tool with a clearer CLI.

## Service vs legacy CLI (2026-07-14)

| | Legacy (`src/` + `main.py`) | New service (`app/` + Compose) |
|--|------------------------------|--------------------------------|
| How you run it | Local CLI on one PDF | HTTP: upload PDF, poll, download MD |
| Dependencies | **One** venv (MinerU+MolScribe+API together) | **Three** venvs; API never imports ML |
| Env conflict | Pin `albumentations` / skip vLLM | Isolation like Wipo `chemistry_extractor` |
| Deploy | Manual Python | `docker compose up` |
| OCR focus | Same MinerU OCR + MolScribe replace | Same pipeline, job queue (`max_workers=1`) |
| Output | Local `*_final.md` | `GET /v1/jobs/{id}/markdown` |

Core OCR→SMILES idea is the same; packaging and conflict strategy are what changed.
