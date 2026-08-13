# Profuomo Upload Safety Design

## Goal

Prevent the Profuomo workflow from creating duplicate Magento products, invalid size variants, or oversized/mixed image galleries.

## Scope

The change applies only to the Profuomo product scrape and Magento product-registration workflow. VENTI/Casamoda behavior, stock retrieval, and stock imports remain unchanged.

## Design

### Duplicate prevention

Magento catalog export handling becomes fail-closed. The uploader accepts only the specifically downloaded export file, validates that it has a non-empty `SKU` column, normalizes SKU values, and refuses to continue if export acquisition or parsing fails.

Before product creation, every queued parent SKU is checked against:

- the validated Magento export;
- the current run's already-seen set.

An existing Magento parent SKU, or a parent with existing child SKUs, is marked done and excluded. The Profuomo stock input is not treated as proof that a Magento product exists because it may legitimately contain products not yet sold in Magento; the validated Magento catalog remains the authority for creation eligibility.

### Stale queue handling

`failed.txt` remains diagnostic history, not a permanent block list. Failed products may be retried, but only after passing the validated Magento check. Existing products discovered during that check are removed from the upload set and recorded in `done.txt` so stale rows in `products/all.csv` cannot return on later runs.

### Size validation

Profuomo size extraction is normalized by category before CSV persistence and again before Magento configuration:

- Shirts: numeric collar sizes `35` through `50` only.
- Knitwear, Polos, Overshirts: alpha sizes `XS` through `XXXL` only.
- Unknown categories retain existing normalized behavior to avoid unrelated regressions.

Products with no valid sizes after filtering are not saved for upload.

### Image hygiene

Profuomo scraping clears the target SKU image folder before a fresh download. Image candidates must be SKU-linked when their URL exposes a SKU, duplicate content is discarded, and no more than eight images are retained. Magento uploads only those bounded files.

### Diagnostics

Export validation and skip reasons are written to the existing Profuomo debug log and surfaced in the returned error message. No workflow silently continues after a failed duplicate check.

## Testing

Regression tests cover malformed/wrong exports, normalized existing-SKU filtering, stale queued products, category-specific sizes, image folder cleanup, deduplication, and upload limits. Existing Magento and Casamoda tests must remain green.
