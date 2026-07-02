import json
import tempfile
import unittest
from pathlib import Path

from automations.casamoda import (
    CasamodaColorMiss,
    CasamodaColorMap,
    CasamodaParser,
    CasamodaPriceList,
    CasamodaScraper,
    PriceMiss,
    UnknownPriceError,
)


DETAIL_HTML = """
<html>
  <body>
    <h1>Businesshemd</h1>
    <div data-mass-order data-vendor-key="123942200">
      <p>Artikelnummer: 123942200</p>
      <section>
        <h2>Produktdetails</h2>
        <ul>
          <li>Passform: Modern Fit</li>
          <li>Armlänge: Langarm</li>
          <li>Kragenform: Kent-Kragen</li>
          <li>Muster: uni</li>
          <li>Material: 100 % Baumwolle</li>
          <li>Stoffart: Popeline</li>
        </ul>
      </section>
      <p>Produktinformationen</p>
      <p>Ein klassisches Businesshemd.</p>
      <img src="/media/catalog/product/front.jpg" />
      <div class="order-grid">
        <h3>000 weiss <span>SALE</span></h3>
        <input data-quantity data-variant-id="v36" data-item-size="36"
               max="12" data-item-list-price="23100"
               data-item-selling-price="13860" data-item-retail-price="59990" />
        <input data-quantity data-variant-id="v37" data-item-size="37"
               max="0" data-item-list-price="23100"
               data-item-selling-price="13860" data-item-retail-price="59990" />
        <input data-quantity data-variant-id="v47" data-item-size="47"
               max="5" data-item-list-price="25450"
               data-item-selling-price="15250" data-item-retail-price="64990" />
      </div>
    </div>
  </body>
</html>
"""


class CasamodaPriceListTests(unittest.TestCase):
    def test_lookup_uses_exact_price_list_and_prompt_defaults(self):
        price_list = CasamodaPriceList.from_rows([("23,10", "59,99")])

        self.assertEqual(price_list.lookup("23.10"), "59.99")
        self.assertEqual(price_list.lookup("25.45"), "64.99")
        self.assertEqual(price_list.lookup("27.75"), "69.99")

    def test_unknown_purchase_price_is_blocking(self):
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99")], include_prompt_defaults=False
        )

        with self.assertRaises(UnknownPriceError) as raised:
            price_list.lookup("25.45")

        self.assertIn("25.45", str(raised.exception))


class CasamodaParserTests(unittest.TestCase):
    def test_product_details_do_not_merge_nos_into_stoffart(self):
        details = CasamodaParser._product_details(
            """
            <section>
              <h2>Produktdetails</h2>
              <ul>
                <li>Stoffart: Popeline</li>
                <li>NOS: Ja</li>
              </ul>
            </section>
            """
        )

        self.assertEqual(details["Stoffart"], "Popeline")

    def test_image_urls_keep_gallery_images_once_and_drop_swatch_thumbnails(self):
        html = """
        <img src="/articles/front.jpg?auto=format&amp;bg=F3F3F3&amp;pad=5">
        <img src="/articles/front.jpg?auto=format">
        <img src="/articles/front.jpg?auto=format&amp;bg=F3F3F3&amp;h=50&amp;w=50">
        <img src="/articles/detail.jpg?auto=format&amp;bg=F3F3F3&amp;pad=20">
        <img src="/articles/detail.jpg?auto=format">
        """

        urls = CasamodaParser._image_urls(
            html,
            "https://b2b.casamoda.com/de/de/article/3901/50",
        )

        self.assertEqual(
            urls,
            [
                "https://b2b.casamoda.com/articles/front.jpg?auto=format",
                "https://b2b.casamoda.com/articles/detail.jpg?auto=format",
            ],
        )

    def test_image_urls_for_farbnummer_prefer_matching_color_code(self):
        html = """
        <img src="/articles/123955800_000_front.jpg?auto=format">
        <img src="/articles/123955800_000_detail.jpg?auto=format">
        <img src="/articles/123955800_100_front.jpg?auto=format">
        <img src="/articles/123955800_100_detail.jpg?auto=format">
        """

        urls = CasamodaParser._image_urls_for_farbnummer(
            html,
            "https://b2b.casamoda.com/de/de/article/3901/50",
            "100",
        )

        self.assertEqual(
            urls,
            [
                "https://b2b.casamoda.com/articles/123955800_100_front.jpg?auto=format",
                "https://b2b.casamoda.com/articles/123955800_100_detail.jpg?auto=format",
            ],
        )

    def test_listing_links_keep_color_specific_urls(self):
        links = CasamodaParser.parse_listing_links(
            """
            <a href="/de/de/article/3901/50">000 weiss</a>
            <a href="/de/de/article/3901/51">001 weiss</a>
            """,
            "https://b2b.casamoda.com/de/de/article_collection/product-list-venti--modern-fit",
        )

        self.assertEqual(
            links,
            [
                "https://b2b.casamoda.com/de/de/article/3901/50",
                "https://b2b.casamoda.com/de/de/article/3901/51",
            ],
        )

    def test_parse_detail_uses_normal_net_price_per_size(self):
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99"), ("25.45", "64.99")]
        )
        rows = CasamodaParser(price_list).parse_product_detail(
            DETAIL_HTML,
            "https://b2b.casamoda.com/de/de/article/3901/50",
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["supplier"], "casamoda")
        self.assertEqual(row["brand"], "VENTI")
        self.assertEqual(row["article_number"], "123942200")
        self.assertEqual(row["farbnummer"], "000")
        self.assertEqual(row["sku"], "123942200-000")
        self.assertEqual(row["rrp"], "59.99")
        self.assertEqual(row["sizes"], "['36', '37', '47']")
        self.assertEqual(row["fit"], "Modern Fit")
        self.assertEqual(row["quality"], "Popeline")
        self.assertEqual(row["collar"], "Kent-Kragen")
        self.assertEqual(row["fabriccomp"], "100 % Baumwolle")

        variant_prices = json.loads(row["variant_prices"])
        purchase_prices = json.loads(row["purchase_prices"])
        self.assertEqual(variant_prices["36"], "59.99")
        self.assertEqual(variant_prices["47"], "64.99")
        self.assertEqual(purchase_prices["36"], "23.10")
        self.assertEqual(purchase_prices["47"], "25.45")

    def test_parse_detail_falls_back_to_selling_price_when_list_price_is_blank(self):
        html = DETAIL_HTML.replace(
            'data-item-list-price="25450"\n               data-item-selling-price="15250"',
            'data-item-list-price=""\n               data-item-selling-price="26950"',
        )
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99"), ("26.95", "69.99")]
        )
        rows = CasamodaParser(price_list).parse_product_detail(
            html,
            "https://b2b.casamoda.com/de/de/article/3901/50",
        )

        variant_prices = json.loads(rows[0]["variant_prices"])
        purchase_prices = json.loads(rows[0]["purchase_prices"])
        self.assertEqual(variant_prices["47"], "69.99")
        self.assertEqual(purchase_prices["47"], "26.95")

    def test_parse_detail_groups_live_variant_cells_by_farbnummer(self):
        html = DETAIL_HTML
        html = html.replace(
            'data-item-size="36"', 'data-article-price-block="23100-000-1" data-item-size="36"'
        )
        html = html.replace(
            'data-item-size="37"', 'data-article-price-block="23100-000-1" data-item-size="37"'
        )
        html = html.replace(
            'data-item-size="47"', 'data-article-price-block="25450-000-1" data-item-size="47"'
        )
        html = html.replace(
            "      </div>\n    </div>\n  </body>",
            """
        <input data-quantity data-variant-id="blue36"
               data-article-price-block="26950-100-1" data-item-size="36"
               max="3" data-item-list-price=""
               data-item-selling-price="26950" data-item-retail-price="69990" />
      </div>
    </div>
  </body>""",
        )
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99"), ("25.45", "64.99"), ("26.95", "69.99")]
        )

        rows = CasamodaParser(price_list).parse_product_detail(
            html,
            "https://b2b.casamoda.com/de/de/article/3901/50",
        )

        self.assertEqual([row["sku"] for row in rows], ["123942200-000", "123942200-100"])
        self.assertEqual(rows[1]["color"], "Blauw")
        self.assertEqual(json.loads(rows[1]["purchase_prices"])["36"], "26.95")

    def test_color_map_from_rows_controls_farbnummer_color(self):
        html = DETAIL_HTML.replace(
            'data-item-size="36"',
            'data-article-price-block="23100-250-1" data-item-size="36"',
        )
        html = html.replace(
            'data-item-size="37"',
            'data-article-price-block="23100-250-1" data-item-size="37"',
        )
        html = html.replace(
            'data-item-size="47"',
            'data-article-price-block="25450-250-1" data-item-size="47"',
        )
        color_map = CasamodaColorMap.from_rows(
            [
                ("Van", "Tot", "Kleur (Duits)", "Kleur (Nederlands)"),
                ("200", "299", "braun", "Bruin"),
            ]
        )
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99"), ("25.45", "64.99")]
        )

        rows = CasamodaParser(price_list, color_map=color_map).parse_product_detail(
            html,
            "https://b2b.casamoda.com/de/de/article/3901/50",
        )

        self.assertEqual(rows[0]["farbnummer"], "250")
        self.assertEqual(rows[0]["color"], "Bruin")

    def test_missing_color_code_is_marked_and_reported(self):
        html = DETAIL_HTML.replace(
            'data-item-size="36"',
            'data-article-price-block="23100-999-1" data-item-size="36"',
        )
        html = html.replace(
            'data-item-size="37"',
            'data-article-price-block="23100-999-1" data-item-size="37"',
        )
        html = html.replace(
            'data-item-size="47"',
            'data-article-price-block="25450-999-1" data-item-size="47"',
        )
        misses = []
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99"), ("25.45", "64.99")]
        )

        rows = CasamodaParser(
            price_list,
            color_map=CasamodaColorMap.from_rows([]),
            missing_color_callback=misses.append,
        ).parse_product_detail(
            html,
            "https://b2b.casamoda.com/de/de/article/3901/50",
        )

        self.assertEqual(rows[0]["farbnummer"], "999")
        self.assertEqual(rows[0]["color"], "")
        self.assertEqual(rows[0]["color_missing"], "True")
        self.assertEqual(len(misses), 1)
        self.assertEqual(misses[0].article_number, "123942200")
        self.assertEqual(misses[0].farbnummer, "999")

    def test_parse_detail_assigns_color_specific_images_to_each_row(self):
        html = DETAIL_HTML.replace(
            '<img src="/media/catalog/product/front.jpg" />',
            """
            <img src="/media/catalog/product/123942200_000_front.jpg?auto=format" />
            <img src="/media/catalog/product/123942200_100_front.jpg?auto=format" />
            """,
        )
        html = html.replace(
            'data-item-size="36"', 'data-article-price-block="23100-000-1" data-item-size="36"'
        )
        html = html.replace(
            'data-item-size="37"', 'data-article-price-block="23100-000-1" data-item-size="37"'
        )
        html = html.replace(
            'data-item-size="47"', 'data-article-price-block="25450-000-1" data-item-size="47"'
        )
        html = html.replace(
            "      </div>\n    </div>\n  </body>",
            """
        <input data-quantity data-variant-id="blue36"
               data-article-price-block="26950-100-1" data-item-size="36"
               max="3" data-item-list-price=""
               data-item-selling-price="26950" data-item-retail-price="69990" />
      </div>
    </div>
  </body>""",
        )
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99"), ("25.45", "64.99"), ("26.95", "69.99")]
        )

        rows = CasamodaParser(price_list).parse_product_detail(
            html,
            "https://b2b.casamoda.com/de/de/article/3901/50",
        )

        self.assertEqual(
            json.loads(rows[0]["image_urls"]),
            [
                "https://b2b.casamoda.com/media/catalog/product/123942200_000_front.jpg?auto=format"
            ],
        )
        self.assertEqual(
            json.loads(rows[1]["image_urls"]),
            [
                "https://b2b.casamoda.com/media/catalog/product/123942200_100_front.jpg?auto=format"
            ],
        )

    def test_parse_detail_blocks_when_any_size_price_is_unknown(self):
        price_list = CasamodaPriceList.from_rows(
            [("23.10", "59.99")], include_prompt_defaults=False
        )

        with self.assertRaises(UnknownPriceError) as raised:
            CasamodaParser(price_list).parse_product_detail(
                DETAIL_HTML,
                "https://b2b.casamoda.com/de/de/article/3901/50",
            )

        self.assertIn("123942200", str(raised.exception))
        self.assertIn("25.45", str(raised.exception))


class CasamodaScraperTests(unittest.TestCase):
    def test_category_slug_from_url(self):
        self.assertEqual(
            CasamodaScraper._category_slug_from_url(
                "https://b2b.casamoda.com/de/de/article_collection/product-list-venti--modern-fit"
            ),
            "venti_modern_fit",
        )
        self.assertEqual(
            CasamodaScraper._category_slug_from_url(
                "https://b2b.casamoda.com/de/de/article_collection/product-list-venti--polos--shirts"
            ),
            "venti_polos_shirts",
        )
        self.assertEqual(
            CasamodaScraper._category_slug_from_url(
                "https://b2b.casamoda.com/de/de/article_collection/product-list-venti-evening"
            ),
            "venti_evening",
        )

    def test_scrape_venti_without_url_uses_autoimport_categories(self):
        class RecordingScraper(CasamodaScraper):
            calls: list[tuple[str, str | None]] = []

            def scrape_category(self, url: str):
                self.calls.append(("category", url))
                return {"message": "category", "error": ""}

            def scrape_autoimport_categories(self):
                self.calls.append(("autoimport", None))
                return {"message": "autoimport", "error": ""}

        RecordingScraper.calls = []

        status = RecordingScraper.scrape_venti(username="user", password="pass")

        self.assertEqual(status["message"], "autoimport")
        self.assertEqual(RecordingScraper.calls, [("autoimport", None)])

    def test_scrape_venti_with_url_uses_only_that_category(self):
        category_url = (
            "https://b2b.casamoda.com/de/de/article_collection/"
            "product-list-venti--polos--shirts"
        )

        class RecordingScraper(CasamodaScraper):
            calls: list[tuple[str, str]] = []

            def scrape_category(self, url: str):
                self.calls.append(("category", url))
                return {"message": "category", "error": ""}

        RecordingScraper.calls = []

        status = RecordingScraper.scrape_venti(
            category_url,
            username="user",
            password="pass",
        )

        self.assertEqual(status["message"], "category")
        self.assertEqual(RecordingScraper.calls, [("category", category_url)])

    def test_resolve_autoimport_urls_reads_comments_and_deduplicates(self):
        first_url = (
            "https://b2b.casamoda.com/de/de/article_collection/"
            "product-list-venti--modern-fit"
        )
        second_url = (
            "https://b2b.casamoda.com/de/de/article_collection/"
            "product-list-venti--comfort-fit"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            autoimport_path = Path(temp_dir) / "autoimport.txt"
            autoimport_path.write_text(
                "\n".join(
                    [
                        "# VENTI categories",
                        first_url,
                        "",
                        second_url,
                        first_url,
                    ]
                ),
                encoding="utf-8",
            )
            scraper = CasamodaScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )

            self.assertEqual(
                scraper.resolve_scrape_urls(None),
                [first_url, second_url],
            )

    def test_autoimport_file_is_created_with_top_level_venti_categories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = CasamodaScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )

            scraper.ensure_autoimport_file()

            lines = [
                line.strip()
                for line in (Path(temp_dir) / "autoimport.txt").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            self.assertIn(
                "https://b2b.casamoda.com/de/de/article_collection/product-list-venti--modern-fit",
                lines,
            )
            self.assertIn(
                "https://b2b.casamoda.com/de/de/article_collection/product-list-venti--sakkos--westen",
                lines,
            )
            self.assertNotIn(
                "https://b2b.casamoda.com/de/de/article_collection/product-list-venti--accessoires",
                lines,
            )
            self.assertFalse(any("?page=" in line for line in lines))
            self.assertFalse(any("/de/en/" in line for line in lines))

    def test_scrape_autoimport_categories_scrapes_each_configured_url(self):
        first_url = (
            "https://b2b.casamoda.com/de/de/article_collection/"
            "product-list-venti--modern-fit"
        )
        second_url = (
            "https://b2b.casamoda.com/de/de/article_collection/"
            "product-list-venti--comfort-fit"
        )

        class FakeScraper(CasamodaScraper):
            calls: list[str] = []

            def scrape_category(self, url: str, **kwargs):
                self.calls.append(url)
                return {
                    "message": "category",
                    "error": "",
                    "products": 2 if url == first_url else 3,
                    "unknown_prices": 1 if url == second_url else 0,
                    "csv_path": f"{url}.csv",
                    "all_csv_path": "ignored",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "autoimport.txt").write_text(
                f"{first_url}\n{second_url}\n",
                encoding="utf-8",
            )
            FakeScraper.calls = []
            scraper = FakeScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )

            status = scraper.scrape_autoimport_categories()

            self.assertEqual(FakeScraper.calls, [first_url, second_url])
            self.assertEqual(status["products"], 5)
            self.assertEqual(status["unknown_prices"], 1)
            self.assertEqual(status["categories"], 2)
            self.assertEqual(
                status["all_csv_path"],
                str(Path(temp_dir) / "products" / "venti_all.csv"),
            )

    def test_merge_category_csvs_refreshes_venti_all_csv_when_no_category_rows_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            products_dir = Path(temp_dir) / "products"
            products_dir.mkdir()
            stale_venti_all_csv = products_dir / "venti_all.csv"
            stale_venti_all_csv.write_text('"sku"\n"old-sku"\n', encoding="utf-8")
            scraper = CasamodaScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )

            output_path = scraper._merge_category_csvs()

            self.assertEqual(output_path, stale_venti_all_csv)
            all_csv_text = stale_venti_all_csv.read_text(encoding="utf-8")
            self.assertIn("sku", all_csv_text)
            self.assertNotIn("old-sku", all_csv_text)
            self.assertFalse((products_dir / "all.csv").exists())

    def test_merge_category_csvs_removes_legacy_casamoda_all_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            products_dir = Path(temp_dir) / "products"
            products_dir.mkdir()
            (products_dir / "all.csv").write_text(
                '"sku"\n"legacy-sku"\n',
                encoding="utf-8",
            )
            (products_dir / "venti_modern_fit.csv").write_text(
                '"sku","name"\n"new-sku","New product"\n',
                encoding="utf-8",
            )
            scraper = CasamodaScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )

            output_path = scraper._merge_category_csvs()

            self.assertEqual(output_path, products_dir / "venti_all.csv")
            self.assertFalse((products_dir / "all.csv").exists())
            merged_text = output_path.read_text(encoding="utf-8")
            self.assertIn("new-sku", merged_text)
            self.assertNotIn("legacy-sku", merged_text)

    def test_unknown_prices_are_written_to_central_file_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = CasamodaScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )
            scraper._ensure_dirs()
            miss = PriceMiss(
                article_number="123942200",
                farbnummer="000",
                size="47",
                purchase_price="29.65",
                source_url="https://example.test/product",
            )

            scraper._write_unknown_prices(
                [miss],
                category_slug="venti_modern_fit",
                reset=True,
            )

            central_path = Path(temp_dir) / "unknown_prices.csv"
            category_path = Path(temp_dir) / "unknown_prices_venti_modern_fit.csv"
            self.assertTrue(central_path.exists())
            self.assertFalse(category_path.exists())
            central_text = central_path.read_text(encoding="utf-8")
            self.assertIn("123942200", central_text)
            self.assertIn("29.65", central_text)

    def test_unknown_prices_append_to_central_file_across_categories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = CasamodaScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )
            scraper._ensure_dirs()

            scraper._write_unknown_prices(
                [
                    PriceMiss(
                        article_number="modern",
                        farbnummer="000",
                        size="47",
                        purchase_price="29.65",
                        source_url="https://example.test/modern",
                    )
                ],
                category_slug="venti_modern_fit",
                reset=True,
            )
            scraper._write_unknown_prices(
                [
                    PriceMiss(
                        article_number="body",
                        farbnummer="100",
                        size="46",
                        purchase_price="33.90",
                        source_url="https://example.test/body",
                    )
                ],
                category_slug="venti_body_fit",
            )

            central_text = (Path(temp_dir) / "unknown_prices.csv").read_text(
                encoding="utf-8"
            )
            self.assertEqual(central_text.count("article_number"), 1)
            self.assertIn("modern", central_text)
            self.assertIn("body", central_text)

    def test_missing_color_code_blocks_magento_ready_row(self):
        row = {
            "name": "Businesshemd",
            "fit": "Modern Fit",
            "farbnummer": "999",
            "color_missing": "True",
        }

        CasamodaScraper._apply_category_metadata(
            row,
            "venti_modern_fit",
            "https://example/modern-fit",
        )

        self.assertEqual(row["category"], "shirts")
        self.assertEqual(row["magento_ready"], "False")
        self.assertIn("kleurcodes.xlsx", row["blocked_reason"])
        self.assertIn("999", row["blocked_reason"])

    def test_missing_color_codes_are_written_to_log_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = CasamodaScraper(
                username="user",
                password="pass",
                base_dir=temp_dir,
            )
            scraper._ensure_dirs()
            miss = CasamodaColorMiss(
                article_number="123942200",
                farbnummer="999",
                source_url="https://example.test/product",
            )

            scraper._write_missing_color_codes([miss], reset=True)

            report_path = Path(temp_dir) / "logs" / "missing_color_codes.csv"
            self.assertTrue(report_path.exists())
            report_text = report_path.read_text(encoding="utf-8")
            self.assertIn("123942200", report_text)
            self.assertIn("999", report_text)

    def test_shirt_family_categories_are_magento_ready(self):
        ready_slugs = [
            "venti_modern_fit",
            "venti_body_fit",
            "venti_comfort_fit",
            "venti_jerseyflex",
            "venti_evening",
        ]
        for slug in ready_slugs:
            row = {"name": "Businesshemd", "fit": "Body Fit"}

            CasamodaScraper._apply_category_metadata(row, slug, f"https://example/{slug}")

            self.assertEqual(row["category"], "shirts")
            self.assertEqual(row["magento_ready"], "True")
            self.assertEqual(row["blocked_reason"], "")

    def test_non_shirt_categories_stay_review_only(self):
        row = {"name": "Sakko", "fit": "Basic Sakko"}

        CasamodaScraper._apply_category_metadata(
            row,
            "venti_sakkos_westen",
            "https://example/sakkos",
        )

        self.assertEqual(row["category"], "review")
        self.assertEqual(row["magento_ready"], "False")
        self.assertIn("review only", row["blocked_reason"])

    def test_venti_tshirts_and_tanktops_are_ready_for_ondershirts(self):
        for name in ("T-Shirt Doppelpack", "Tanktop im Doppelpack"):
            row = {"name": name, "fit": "Basic Circular Knit"}

            CasamodaScraper._apply_category_metadata(
                row,
                "venti_polos_shirts",
                "https://example/polos-shirts",
            )

            self.assertEqual(row["category"], "t-shirts")
            self.assertEqual(row["fit"], "Modern Fit")
            self.assertEqual(row["magento_ready"], "True")
            self.assertEqual(row["blocked_reason"], "")

    def test_venti_polos_stay_review_only_until_confirmed(self):
        row = {"name": "Poloshirt", "fit": "Modern Fit"}

        CasamodaScraper._apply_category_metadata(
            row,
            "venti_polos_shirts",
            "https://example/polos-shirts",
        )

        self.assertEqual(row["category"], "polos")
        self.assertEqual(row["magento_ready"], "False")
        self.assertIn("Polos/Shirts category", row["blocked_reason"])


if __name__ == "__main__":
    unittest.main()
