import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from urllib3.exceptions import ReadTimeoutError

from automations.magento import MagentoFiller
from automations.supplier_profile import CASAMODA_VENTI_PROFILE, SupplierProfile


class MagentoFillerTests(unittest.TestCase):
    def test_load_existing_magento_skus_rejects_export_without_sku_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "export.csv"
            pd.DataFrame({"Name": ["Existing product"]}).to_csv(
                export_path,
                index=False,
            )

            with self.assertRaisesRegex(ValueError, "SKU"):
                MagentoFiller.load_existing_magento_skus(export_path)

    def test_load_existing_magento_skus_normalizes_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "export.csv"
            pd.DataFrame(
                {
                    "SKU": [" PP2HC10008 ", "pp2j00001c-s", ""],
                    "Type": [
                        "Configurable Product",
                        "Simple Product",
                        "Simple Product",
                    ],
                }
            ).to_csv(export_path, index=False)

            existing_skus = MagentoFiller.load_existing_magento_skus(
                export_path,
                require_product_types=True,
            )

        self.assertEqual(
            existing_skus,
            {"PP2HC10008", "PP2J00001C-S"},
        )

    def test_load_existing_magento_skus_rejects_filtered_single_type_export(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "export.csv"
            pd.DataFrame(
                {
                    "SKU": ["PP2HC10008", "PP2HC10012"],
                    "Type": ["Configurable Product", "Configurable Product"],
                }
            ).to_csv(export_path, index=False)

            with self.assertRaisesRegex(ValueError, "filtered or incomplete"):
                MagentoFiller.load_existing_magento_skus(
                    export_path,
                    require_product_types=True,
                )

    def test_partition_products_blocks_parent_when_parent_or_child_sku_exists(self):
        products = pd.DataFrame(
            {
                "sku": ["PP2HC10008", "PP2J00001C", "PPNEW0001"],
                "sizes": ["['37']", "['S']", "['M']"],
            }
        )

        new_products, blocked_products = MagentoFiller.partition_products_by_existing_sku(
            products,
            {"PP2HC10008", "PP2J00001C-S"},
        )

        self.assertEqual(new_products["sku"].tolist(), ["PPNEW0001"])
        self.assertEqual(
            blocked_products["sku"].tolist(),
            ["PP2HC10008", "PP2J00001C"],
        )

    def test_partition_products_keeps_one_row_per_new_parent_sku(self):
        products = pd.DataFrame(
            {
                "sku": ["PPNEW0001", " ppnew0001 ", "PPNEW0002"],
                "sizes": ["['M']", "['L']", "['XL']"],
            }
        )

        new_products, blocked_products = MagentoFiller.partition_products_by_existing_sku(
            products,
            set(),
        )

        self.assertEqual(new_products["sku"].tolist(), ["PPNEW0001", "PPNEW0002"])
        self.assertTrue(blocked_products.empty)

    def test_sanitize_profuomo_products_removes_stale_invalid_sizes(self):
        products = pd.DataFrame(
            {
                "sku": ["PP2HC10008", "PP2HCINVALID"],
                "category": ["Shirts", "Shirts"],
                "sizes": [
                    "['37', '38', 'M', '20']",
                    "['M', '20']",
                ],
            }
        )

        sanitized = MagentoFiller.sanitize_profuomo_products(products)

        self.assertEqual(sanitized["sku"].tolist(), ["PP2HC10008"])
        self.assertEqual(sanitized.iloc[0]["sizes"], "['37', '38']")

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

    def test_venti_color_from_color_code_map_does_not_need_translation(self):
        old_profile = MagentoFiller.ACTIVE_PROFILE
        old_mapping = getattr(MagentoFiller, "TRANSLATE_MAPPING", {}).copy()

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = SupplierProfile(
                key="casamoda_venti",
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
            profile.translation_mapping_path.write_text("Blau : Blauw\n", encoding="utf-8")
            MagentoFiller.ACTIVE_PROFILE = profile
            MagentoFiller.TRANSLATE_MAPPING = {"blau": "Blauw"}

            try:
                data = MagentoFiller.fetch_data(
                    pd.Series(
                        {
                            "sku": "226110600-100",
                            "category": "shirts",
                            "color": "Blauw",
                        }
                    )
                )
            finally:
                MagentoFiller.ACTIVE_PROFILE = old_profile
                MagentoFiller.TRANSLATE_MAPPING = old_mapping

            report_path = base / "logs" / "missing_magento_specs.csv"

        self.assertIn("Blauw", data)
        self.assertEqual(
            data["Blauw"],
            (
                "LABEL_SELECT",
                "Kleuren",
            ),
        )
        self.assertFalse(report_path.exists())

    def test_labeled_select_option_finds_field_by_visible_label(self):
        class FakeSelect:
            pass

        class FakeDriver:
            def __init__(self):
                self.select = FakeSelect()
                self.calls = []

            def execute_script(self, script, *args):
                self.calls.append((script, args))
                if "document.querySelectorAll" in script:
                    return self.select
                if "scrollIntoView" in script:
                    return None
                if "const select = arguments[0]" in script:
                    return args == (self.select, "groen")
                raise AssertionError("Unexpected script")

        driver = FakeDriver()

        MagentoFiller.set_labeled_select_option_by_title(
            driver,
            "Kleuren",
            "Groen",
            timeout=1,
        )

        self.assertEqual(len(driver.calls), 3)

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

            def execute_script(self, script, element=None):
                self.scripts.append((script, element))

        element = InterceptedElement()
        driver = FakeDriver()

        MagentoFiller.click_element_safely(driver, element)

        self.assertEqual(element.clicks, 1)
        self.assertEqual(len(driver.scripts), 2)
        self.assertIn("scrollIntoView", driver.scripts[0][0])
        self.assertIn("click", driver.scripts[1][0])

    def test_wait_for_magento_admin_idle_requires_consecutive_quiet_polls(self):
        class FakeDriver:
            def __init__(self):
                self.states = [False, True, False, True, True]
                self.calls = 0

            def execute_script(self, script):
                self.calls += 1
                return self.states.pop(0)

        driver = FakeDriver()

        with patch("automations.magento.time.sleep"):
            self.assertTrue(
                MagentoFiller.wait_for_magento_admin_idle(
                    driver,
                    timeout=1,
                    poll=0.01,
                    stable_polls=2,
                )
            )

        self.assertEqual(driver.calls, 5)

    def test_wait_for_product_save_result_accepts_saved_edit_url(self):
        class FakeDriver:
            current_url = "https://luxeoverhemden.nl/admin/catalog/product/edit/id/123/"

            def find_elements(self, by, value):
                return []

        result = MagentoFiller.wait_for_product_save_result(
            FakeDriver(),
            "https://luxeoverhemden.nl/admin/catalog/product/new/",
            timeout=1,
        )

        self.assertEqual(result, "saved")

    def test_wait_for_product_save_result_reports_driver_read_timeout(self):
        class FakeDriver:
            current_url = "https://luxeoverhemden.nl/admin/catalog/product/new/"

            def find_elements(self, by, value):
                raise ReadTimeoutError(None, None, "driver did not respond")

        result = MagentoFiller.wait_for_product_save_result(
            FakeDriver(),
            "https://luxeoverhemden.nl/admin/catalog/product/new/",
            timeout=1,
        )

        self.assertEqual(result, "driver_timeout")

    def test_save_product_uses_primary_save_and_recovers_stuck_loader(self):
        class FakeElement:
            def __init__(self, name, clicks, driver):
                self.name = name
                self.clicks = clicks
                self.driver = driver

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                self.clicks.append(self.name)
                self.driver.current_url = (
                    "https://luxeoverhemden.nl/admin/catalog/product/edit/id/123/"
                )

        class FakeDriver:
            def __init__(self):
                self.clicks = []
                self.current_url = "https://luxeoverhemden.nl/admin/catalog/product/new/"
                self.refreshes = 0

            def find_element(self, by, value):
                if by == By.CSS_SELECTOR and value == "#save, button[data-ui-id='save-button']":
                    return FakeElement("save", self.clicks, self)
                raise AssertionError(f"Unexpected locator: {by}={value}")

            def execute_script(self, script, element=None):
                if element is not None and "button.click" in script:
                    element.click()
                return None

            def refresh(self):
                self.refreshes += 1

        calls = []

        def fake_wait(cls, driver, **kwargs):
            calls.append(kwargs.get("context"))
            if kwargs.get("context") == "after product save click":
                raise TimeoutException("loader stayed visible")
            return True

        with patch.object(
            MagentoFiller,
            "wait_for_magento_admin_idle",
            new=classmethod(fake_wait),
        ), patch.object(
            MagentoFiller,
            "wait_for_product_save_result",
            return_value="saved",
        ):
            driver = FakeDriver()
            MagentoFiller.save_product(driver)

        self.assertEqual(
            calls,
            [
                "before opening product save menu",
                "after product save click",
                "after product save refresh",
            ],
        )
        self.assertEqual(driver.clicks, ["save"])
        self.assertEqual(driver.refreshes, 1)

    def test_save_product_refreshes_immediately_when_save_poll_hits_driver_timeout(self):
        class FakeElement:
            def __init__(self, clicks):
                self.clicks = clicks

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                self.clicks.append("save")

        class FakeDriver:
            current_url = "https://luxeoverhemden.nl/admin/catalog/product/new/"

            def __init__(self):
                self.clicks = []
                self.refreshes = 0

            def find_element(self, by, value):
                if by == By.CSS_SELECTOR and value == "#save, button[data-ui-id='save-button']":
                    return FakeElement(self.clicks)
                raise AssertionError(f"Unexpected locator: {by}={value}")

            def execute_script(self, script, element=None):
                if element is not None and "button.click" in script:
                    element.click()
                return None

            def refresh(self):
                self.refreshes += 1

        calls = []

        def fake_wait(cls, driver, **kwargs):
            calls.append(kwargs.get("context"))
            return True

        with patch.object(
            MagentoFiller,
            "wait_for_magento_admin_idle",
            new=classmethod(fake_wait),
        ), patch.object(
            MagentoFiller,
            "wait_for_product_save_result",
            return_value="driver_timeout",
        ):
            driver = FakeDriver()
            MagentoFiller.save_product(driver)

        self.assertEqual(
            calls,
            [
                "before opening product save menu",
                "after product save refresh",
            ],
        )
        self.assertEqual(driver.clicks, ["save"])
        self.assertEqual(driver.refreshes, 1)

    def test_register_product_reopens_form_after_primary_save(self):
        class FakeElement:
            pass

        class FakeDriver:
            def find_element(self, by, value):
                if by == By.NAME and value == "product[name]":
                    return FakeElement()
                if by == By.CSS_SELECTOR and value == "[data-ui-id='messages-message-error']":
                    raise NoSuchElementException()
                raise AssertionError(f"Unexpected locator: {by}={value}")

            def execute_script(self, script):
                return {}

        calls = []

        with patch.object(MagentoFiller, "fill_form", side_effect=lambda *_, **__: calls.append("fill")), patch.object(
            MagentoFiller,
            "save_product",
            side_effect=lambda *_, **__: calls.append("save"),
        ), patch.object(
            MagentoFiller,
            "go_to_product_catalogue",
            side_effect=lambda *_, **__: calls.append("catalogue"),
        ), patch.object(
            MagentoFiller,
            "go_to_form",
            side_effect=lambda *_, **__: calls.append("form"),
        ), patch("automations.magento.time.sleep"):
            MagentoFiller.register_product(FakeDriver(), pd.Series({"sku": "226110550-101"}))

        self.assertEqual(calls, ["fill", "save", "catalogue", "form"])


if __name__ == "__main__":
    unittest.main()
