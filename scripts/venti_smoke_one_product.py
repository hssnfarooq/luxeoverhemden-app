from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from automations.casamoda import (
    CasamodaParser,
    CasamodaScraper,
    VENTI_MODERN_FIT_URL,
)


def _read_lines(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()}


def _existing_magento_skus(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return set(pd.read_csv(path, usecols=["SKU"])["SKU"].astype(str))
    except Exception as ex:
        print(f"Could not read existing Magento export: {ex}")
        return set()


def main() -> None:
    base = Path("Casamoda")
    products_dir = base / "products"
    smoke_csv = products_dir / "venti_smoke_one.csv"
    done = _read_lines(base / "done.txt")
    existing = _existing_magento_skus(Path("magento_products.csv"))

    scraper = CasamodaScraper(progress_callback=print)
    scraper._ensure_dirs()
    price_list = scraper._load_price_list()
    color_map = scraper._load_color_map()
    missing_colors = []
    parser = CasamodaParser(
        price_list,
        color_map=color_map,
        missing_color_callback=missing_colors.append,
    )

    print("Logging in...")
    scraper.login()
    print("Collecting listing links...")
    html = scraper._get(VENTI_MODERN_FIT_URL).text
    links = parser.group_article_urls(
        parser.parse_listing_links(html, VENTI_MODERN_FIT_URL)
    )
    print(f"Candidate article links: {len(links)}")

    selected = None
    selected_images = 0
    for index, link in enumerate(links, start=1):
        print(f"Scanning candidate {index}/{len(links)}: {link}")
        try:
            rows = parser.parse_product_detail(scraper._get(link).text, link)
        except Exception as ex:
            print(f"Skipping article due to parse/price issue: {ex}")
            continue

        for row in rows:
            scraper._apply_category_metadata(
                row,
                "venti_modern_fit",
                VENTI_MODERN_FIT_URL,
            )
            sku = row["sku"]
            if sku in done:
                print(f"Skipping {sku}: already in Casamoda done.txt")
                continue
            if sku in existing:
                print(f"Skipping {sku}: already in magento_products.csv")
                continue
            if row.get("magento_ready") != "True":
                print(
                    f"Skipping {sku}: not ready: {row.get('blocked_reason', '')}"
                )
                continue

            image_urls = json.loads(row.get("image_urls", "[]"))
            if not image_urls:
                print(f"Skipping {sku}: no image URLs")
                continue

            print(f"Downloading {len(image_urls)} images for {sku}...")
            image_count = scraper._download_images(sku, image_urls)
            row["image_count"] = str(image_count)
            row["has_images"] = str(image_count > 0)
            if image_count <= 0:
                print(f"Skipping {sku}: images did not download")
                continue

            selected = row
            selected_images = image_count
            break

        if selected:
            break

    if not selected:
        raise SystemExit("No uploadable VENTI modern-fit product candidate found")

    pd.DataFrame([selected]).to_csv(smoke_csv, index=False)
    print(f"SELECTED_SKU={selected['sku']}")
    print(f"SELECTED_COLOR={selected.get('color')}")
    print(f"SELECTED_FARBNUMMER={selected.get('farbnummer')}")
    print(f"SELECTED_IMAGES={selected_images}")
    print(f"SMOKE_CSV={smoke_csv}")
    print(f"MAGENTO_READY={selected.get('magento_ready')}")
    print(f"BLOCKED_REASON={selected.get('blocked_reason', '')}")
    print(f"MISSING_COLORS={len(missing_colors)}")


if __name__ == "__main__":
    main()
