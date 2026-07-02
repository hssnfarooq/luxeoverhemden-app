import tempfile
import unittest
from pathlib import Path

import pandas as pd
from selenium.common.exceptions import ElementClickInterceptedException

from automations.magento import MagentoFiller
from automations.supplier_profile import CASAMODA_VENTI_PROFILE, SupplierProfile


class MagentoFillerTests(unittest.TestCase):
    def test_venti_collar_and_sleeve_map_to_existing_magento_options(self):
        MagentoFiller.configure_supplier("venti")
        MagentoFiller._get_mapping()

        self.assertEqual(MagentoFiller.get_mapped_key("Kent-Kragen"), "Kent")
        self.assertEqual(MagentoFiller.get_mapped_key("Langarm"), "Lange mouw (normaal)")
        self.assertEqual(
            MagentoFiller.get_mapped_key("Extra lang 72cm"),
            "Extra lange mouw 72 cm",
        )
        self.assertEqual(MagentoFiller.get_mapped_key("Kläppchen-Kragen"), "Wing Collar")
        self.assertEqual(MagentoFiller.get_mapped_key("t-shirts"), "Ondershirts")
        self.assertEqual(MagentoFiller.get_mapped_key("V-Ausschnitt"), "V-hals")
        self.assertEqual(MagentoFiller.get_mapped_key("gestrickt / gewirkt"), "Knitted")

    def test_venti_fit_values_map_to_existing_magento_options(self):
        MagentoFiller.configure_supplier("venti")

        field = "name:product[model]=option:data-title;capitalize"

        self.assertEqual(
            MagentoFiller.format_key(field, "Modern Fit", "shirts"),
            "Modern fit",
        )
        self.assertEqual(
            MagentoFiller.format_key(field, "Body Fit", "shirts"),
            "Body slim fit",
        )
        self.assertEqual(
            MagentoFiller.format_key(field, "Comfort Fit", "shirts"),
            "Comfort fit",
        )
        self.assertEqual(
            MagentoFiller.format_key(field, "Basic Circular Knit", "t-shirts"),
            "Modern fit",
        )

    def test_venti_size_table_uses_fit_and_sleeve(self):
        profile = CASAMODA_VENTI_PROFILE

        self.assertEqual(
            profile.size_table_for(
                source_category="shirts",
                fit="Modern Fit",
                mapped_category="Overhemden",
                sleeve="Langarm",
            ),
            "Venti modern fit overhemden",
        )
        self.assertEqual(
            profile.size_table_for(
                source_category="shirts",
                fit="Modern Fit",
                mapped_category="Overhemden",
                sleeve="Extra lang 72cm",
            ),
            "Venti modern fit overhemden extra lange mouwen 72 cm",
        )
        self.assertEqual(
            profile.size_table_for(
                source_category="shirts",
                fit="Body Fit",
                mapped_category="Overhemden",
                sleeve="Langarm",
            ),
            "Venti body fit overhemden",
        )
        self.assertEqual(
            profile.size_table_for(
                source_category="shirts",
                fit="Comfort Fit",
                mapped_category="Overhemden",
                sleeve="Langarm",
            ),
            "Casa Moda comfort fit",
        )
        self.assertEqual(
            profile.size_table_for(
                source_category="t-shirts",
                fit="Basic Circular Knit",
                mapped_category="Ondershirts",
                sleeve="Kurzarm",
            ),
            "Venti modern fit overhemden",
        )

    def test_missing_magento_specs_are_logged_with_sku_and_source_value(self):
        old_profile = MagentoFiller.ACTIVE_PROFILE
        old_mapping = getattr(MagentoFiller, "TRANSLATE_MAPPING", {}).copy()

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = SupplierProfile(
                key="test",
                name_prefix="Venti",
                manufacturer_title="VENTI",
                products_path=base / "products",
                translation_mapping_path=base / "translate.txt",
                input_csv_path=base / "input.csv",
                debug_log_path=base / "logs" / "debug.txt",
                translation_errors_path=base / "logs" / "translation_errors.log",
                done_path=base / "done.txt",
                failed_path=base / "failed.txt",
            )
            profile.translation_mapping_path.write_text("", encoding="utf-8")
            MagentoFiller.ACTIVE_PROFILE = profile
            MagentoFiller.TRANSLATE_MAPPING = {}

            try:
                MagentoFiller.fetch_data(
                    pd.Series(
                        {
                            "sku": "012500-001",
                            "category": "t-shirts",
                            "quality": "Niet bestaande stof",
                        }
                    )
                )
            finally:
                MagentoFiller.ACTIVE_PROFILE = old_profile
                MagentoFiller.TRANSLATE_MAPPING = old_mapping

            report = (base / "logs" / "missing_magento_specs.csv").read_text(
                encoding="utf-8"
            )

        self.assertIn("012500-001", report)
        self.assertIn("quality", report)
        self.assertIn("Niet bestaande stof", report)
        self.assertIn("missing translation mapping", report)

    def test_normalize_attribute_value_removes_embedded_nos_marker_from_product_name(self):
        value = MagentoFiller.normalize_attribute_value(
            "Productnaam",
            "Venti wit popeline nos: ja",
        )

        self.assertEqual(value, "Venti wit popeline")

    def test_normalize_attribute_value_removes_embedded_nos_marker_from_quality(self):
        value = MagentoFiller.normalize_attribute_value("quality", "Popeline NOS: Ja")

        self.assertEqual(value, "Popeline")

    def test_option_title_xpath_is_scoped_and_case_insensitive(self):
        xpath = MagentoFiller.option_title_xpath("product[manufacturer]", "VENTI")

        self.assertIn("//select[@name=\"product[manufacturer]\"]", xpath)
        self.assertIn("translate(normalize-space(@data-title)", xpath)
        self.assertIn("translate(normalize-space(.)", xpath)
        self.assertIn('="venti"', xpath)

    def test_select_name_from_option_xpath_extracts_scoped_select_name(self):
        select_name = MagentoFiller.select_name_from_option_xpath(
            "//select[@name='product[materiaal]']//option[@data-title='{}']"
        )

        self.assertEqual(select_name, "product[materiaal]")

    def test_variant_size_from_row_text_uses_maat_label_first(self):
        row_text = "Venti wit popeline\nSKU 123942200-000-47\nAttributen\nMaat: 47"

        size = MagentoFiller.variant_size_from_row_text(row_text, {"35", "47"})

        self.assertEqual(size, "47")

    def test_matching_variant_rows_excludes_unrelated_magento_tables(self):
        class FakeRow:
            def __init__(self, text):
                self.text = text

            def get_attribute(self, name):
                if name == "textContent":
                    return self.text
                return ""

        rows = [
            FakeRow("Catalogus\nNaam\nPrijs"),
            FakeRow("Venti wit popeline\nSKU 123942200-000-35\nMaat: 35"),
            FakeRow("Venti wit popeline\nSKU 123942200-000-47\nMaat: 47"),
        ]

        matches = MagentoFiller.matching_variant_rows(None, rows, {"35", "47"})

        self.assertEqual([size for _, size in matches], ["35", "47"])

    def test_ready_variant_row_matches_waits_for_all_expected_sizes(self):
        class FakeRow:
            def __init__(self, text):
                self.text = text

            def get_attribute(self, name):
                if name == "textContent":
                    return self.text
                return ""

        class FakeDriver:
            def __init__(self, rows):
                self.rows = rows

            def find_elements(self, by, selector):
                return self.rows

        incomplete = MagentoFiller.ready_variant_row_matches(
            FakeDriver([FakeRow("Maat: 35")]),
            {"35", "47"},
        )
        complete = MagentoFiller.ready_variant_row_matches(
            FakeDriver([FakeRow("Maat: 35"), FakeRow("Maat: 47")]),
            {"35", "47"},
        )

        self.assertFalse(incomplete)
        self.assertEqual([size for _, size in complete], ["35", "47"])

    def test_variant_price_can_inherit_parent_price_only_when_prices_match(self):
        product = pd.Series({"rrp": "59.99"})

        self.assertTrue(
            MagentoFiller.variant_price_can_inherit_parent_price(product, "59.99")
        )
        self.assertFalse(
            MagentoFiller.variant_price_can_inherit_parent_price(product, "64.99")
        )

    def test_click_element_safely_falls_back_to_javascript_click_when_intercepted(self):
        class InterceptedElement:
            def __init__(self):
                self.clicks = 0

            def click(self):
                self.clicks += 1
                raise ElementClickInterceptedException("covered by sticky header")

        class FakeDriver:
            def __init__(self):
                self.scripts = []

            def execute_script(self, script, element):
                self.scripts.append((script, element))

        element = InterceptedElement()
        driver = FakeDriver()

        MagentoFiller.click_element_safely(driver, element)

        self.assertEqual(element.clicks, 1)
        self.assertEqual(len(driver.scripts), 2)
        self.assertIn("scrollIntoView", driver.scripts[0][0])
        self.assertIn("click", driver.scripts[1][0])


if __name__ == "__main__":
    unittest.main()
