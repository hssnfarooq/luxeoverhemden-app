# Profuomo Upload Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Profuomo product registration fail safely before Magento can create duplicate products, invalid variants, or mixed image galleries.

**Architecture:** Add pure validation helpers around the existing scraper and uploader boundaries. Keep the Selenium flow intact, but require a validated Magento export before creation and sanitize Profuomo sizes/images before they enter persisted CSV or Magento.

**Tech Stack:** Python 3, pandas, Selenium, unittest/pytest-compatible tests.

## Global Constraints

- Apply changes only to Profuomo product scraping and registration.
- Do not change VENTI/Casamoda behavior.
- Do not delete Magento products automatically.
- Duplicate detection must fail closed.

---

### Task 1: Validated Magento duplicate gate

**Files:**
- Modify: `automations/magento.py`
- Test: `tests/test_magento_filler.py`

**Interfaces:**
- Produces: `MagentoFiller.load_existing_magento_skus(path: Path) -> set[str]`
- Produces: normalized filtering through `MagentoFiller.check_existing(...)`

- [ ] Write tests proving malformed exports raise an error and mixed-case/whitespace SKU values are recognized.
- [ ] Run the focused tests and verify they fail because validation is missing.
- [ ] Implement strict export loading, normalized SKU filtering, and done-state updates for existing products.
- [ ] Replace arbitrary newest-CSV selection with detection of a newly downloaded export candidate.
- [ ] Run focused tests and verify they pass.

### Task 2: Profuomo size-family validation

**Files:**
- Modify: `automations/profuomo.py`
- Test: `tests/test_profuomo.py`

**Interfaces:**
- Produces: `ProfuomoScraper.sanitize_sizes_for_category(category: str, sizes: list[str]) -> list[str]`

- [ ] Write tests for shirt numeric sizes, knitwear alpha sizes, and empty invalid results.
- [ ] Run the focused tests and verify they fail because the helper is missing.
- [ ] Implement category-aware sanitization and call it before product persistence.
- [ ] Run focused tests and verify they pass.

### Task 3: Profuomo image hygiene

**Files:**
- Modify: `automations/profuomo.py`
- Test: `tests/test_profuomo.py`

**Interfaces:**
- Produces: bounded, deduplicated files in `products/<SKU>` from `download_images(...)`.

- [ ] Write tests proving stale files are cleared and at most eight unique image payloads remain.
- [ ] Run the focused tests and verify they fail against current accumulation behavior.
- [ ] Implement folder cleanup, content deduplication, and the eight-image limit.
- [ ] Run focused tests and verify they pass.

### Task 4: Verification

**Files:**
- Verify: `automations/magento.py`
- Verify: `automations/profuomo.py`
- Verify: `tests/test_magento_filler.py`
- Verify: `tests/test_profuomo.py`

- [ ] Run focused Profuomo and Magento tests.
- [ ] Run the complete test suite.
- [ ] Compile modified Python modules.
- [ ] Review the diff for VENTI behavior changes and unrelated file churn.
