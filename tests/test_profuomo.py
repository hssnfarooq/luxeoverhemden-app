import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automations.profuomo import Profuomo, ProfuomoDownloader, ProfuomoScraper
from automations.supplier_profile import PROFUOMO_PROFILE
from config import BASE_DIR


class ProfuomoScraperSafetyTests(unittest.TestCase):
    class FakeInput:
        def clear(self):
            return None

        def send_keys(self, *_args):
            return None

        def click(self):
            return None

    class FakeDriver:
        def __init__(self):
            self.visited_urls = []

        def get(self, url):
            self.visited_urls.append(url)

    class FakeClickable:
        def __init__(self, on_click=None):
            self.clicked = False
            self.on_click = on_click

        def click(self):
            self.clicked = True
            if self.on_click:
                self.on_click()

        def is_displayed(self):
            return True

        def is_enabled(self):
            return True

    class FakeCollectionDriver(FakeDriver):
        def __init__(self):
            super().__init__()
            self.dropdown_open = False
            self.collection_selected = False
            self.catalogue_loaded = False
            self.dropdown = ProfuomoScraperSafetyTests.FakeClickable(
                lambda: setattr(self, "dropdown_open", True)
            )
            self.collection = ProfuomoScraperSafetyTests.FakeClickable(
                lambda: setattr(self, "collection_selected", True)
            )
            self.search = ProfuomoScraperSafetyTests.FakeClickable()

        def get(self, url):
            super().get(url)
            if url == "https://b2b.profuomo.com/categories/Micro_Fashion_04":
                self.catalogue_loaded = True

        def find_element(self, by, selector):
            if selector == ".header-collection-dropdown button":
                return self.dropdown
            if "Profuomo AW26 - Re-order" in selector and self.dropdown_open:
                return self.collection
            if selector in {"productSearch", "input#productSearch", "input[name='productSearch']"}:
                if self.catalogue_loaded and self.collection_selected:
                    return self.search
            raise Exception(f"Element not found: {by} {selector}")

        def execute_script(self, _script, element):
            element.click()

    def test_login_uses_current_root_route(self):
        class AuthenticatedProfuomo(Profuomo):
            @classmethod
            def _open_reorder_catalogue(cls, _driver):
                return True

        driver = self.FakeDriver()
        fields = [self.FakeInput(), self.FakeInput(), self.FakeInput()]

        with patch.object(
            AuthenticatedProfuomo, "_find_first", side_effect=fields
        ), patch.object(AuthenticatedProfuomo, "random_wait"), patch.object(
            AuthenticatedProfuomo, "_page_has_authenticated_hint", return_value=True
        ):
            AuthenticatedProfuomo.profuomo_login(driver)

        self.assertEqual(driver.visited_urls, ["https://b2b.profuomo.com/"])

    def test_login_opens_reorder_catalogue_after_authentication(self):
        class CollectionAwareProfuomo(Profuomo):
            @classmethod
            def _open_reorder_catalogue(cls, driver):
                driver.collection_selected = True
                return True

        driver = self.FakeDriver()
        driver.collection_selected = False
        fields = [self.FakeInput(), self.FakeInput(), self.FakeInput()]

        with patch.object(
            CollectionAwareProfuomo, "_find_first", side_effect=fields
        ), patch.object(CollectionAwareProfuomo, "random_wait"), patch.object(
            CollectionAwareProfuomo,
            "_page_has_authenticated_hint",
            return_value=True,
        ):
            CollectionAwareProfuomo.profuomo_login(driver)

        self.assertTrue(driver.collection_selected)

    def test_reorder_catalogue_selection_opens_searchable_category(self):
        driver = self.FakeCollectionDriver()
        open_catalogue = getattr(Profuomo, "_open_reorder_catalogue", lambda _driver: False)

        opened = open_catalogue(driver)

        self.assertTrue(opened)
        self.assertTrue(driver.dropdown.clicked)
        self.assertTrue(driver.collection.clicked)
        self.assertEqual(
            driver.visited_urls,
            ["https://b2b.profuomo.com/categories/Micro_Fashion_04"],
        )

    def test_login_rejects_empty_page_after_form_disappears(self):
        driver = self.FakeDriver()
        fields = [self.FakeInput(), self.FakeInput(), self.FakeInput()]

        with patch.object(Profuomo, "_find_first", side_effect=fields), patch.object(
            Profuomo, "random_wait"
        ), patch.object(
            Profuomo, "_page_has_authenticated_hint", return_value=False
        ), patch.object(Profuomo, "_page_has_login_form", return_value=False):
            with self.assertRaisesRegex(Exception, "authenticated"):
                Profuomo.profuomo_login(driver)

    def test_stock_search_opens_current_category_catalogue(self):
        driver = self.FakeDriver()

        with patch.object(
            ProfuomoDownloader, "_is_browser_crashed", return_value=False
        ), patch.object(ProfuomoDownloader, "_find_first", return_value=None):
            found = ProfuomoDownloader._search_sku_new_flow(driver, "PPXD10018B")

        self.assertFalse(found)
        self.assertEqual(
            driver.visited_urls,
            ["https://b2b.profuomo.com/categories/Micro_Fashion_04"],
        )

    def test_current_category_url_is_recognized_as_listing(self):
        self.assertTrue(
            ProfuomoScraper._looks_like_listing_url(
                "https://b2b.profuomo.com/categories/Micro_Fashion_04"
            )
        )

    def test_profuomo_stock_and_upload_share_root_input_file(self):
        self.assertEqual(PROFUOMO_PROFILE.input_csv_path, Path(BASE_DIR) / "input.csv")

    def test_image_url_rejects_a_different_explicit_color_variant(self):
        self.assertFalse(
            ProfuomoScraper._image_url_matches_target(
                "https://cdn.example/products/PP2J00001B/front.jpg",
                "PP2J00001C",
            )
        )
        self.assertTrue(
            ProfuomoScraper._image_url_matches_target(
                "https://cdn.example/products/PP2J00001/front.jpg",
                "PP2J00001C",
            )
        )

    def test_shirt_sizes_exclude_alpha_and_unrelated_numeric_values(self):
        sizes = ProfuomoScraper.sanitize_sizes_for_category(
            "Shirts",
            ["37", "38", "M", "16", "20", "45"],
        )

        self.assertEqual(sizes, ["37", "38", "45"])

    def test_knitwear_sizes_exclude_numeric_values(self):
        sizes = ProfuomoScraper.sanitize_sizes_for_category(
            "Knitwear",
            ["S", "M", "L", "20", "38", "XXL"],
        )

        self.assertEqual(sizes, ["S", "M", "L", "XXL"])

    def test_download_images_clears_stale_files_deduplicates_and_limits_gallery(self):
        class FakeCookies:
            def set(self, *args, **kwargs):
                return None

        class FakeResponse:
            status_code = 200
            headers = {"Content-Type": "image/jpeg"}

            def __init__(self, content):
                self.content = content

        class FakeSession:
            def __init__(self):
                self.headers = {}
                self.cookies = FakeCookies()

            def get(self, url, timeout):
                marker = "0.jpg" if url.endswith("/1.jpg") else url.rsplit("/", 1)[-1]
                return FakeResponse((marker.encode("ascii") * 6000)[:6000])

        class FakeDriver:
            current_url = "https://b2b.profuomo.com/products/PP2HC10008"

            def get_cookies(self):
                return []

        urls = [f"https://cdn.example/PP2HC10008/{index}.jpg" for index in range(10)]

        with tempfile.TemporaryDirectory() as temp_dir:
            sku_dir = Path(temp_dir) / "PP2HC10008"
            sku_dir.mkdir()
            stale_path = sku_dir / "PP2HC10008_99.jpg"
            stale_path.write_bytes(b"stale" * 2000)

            with patch("automations.profuomo.PRODUCTS_PATH", temp_dir), patch.object(
                ProfuomoScraper,
                "_collect_image_urls",
                return_value=urls,
            ), patch("automations.profuomo.requests.Session", FakeSession):
                count = ProfuomoScraper.download_images(
                    FakeDriver(),
                    "PP2HC10008",
                    target_sku="PP2HC10008",
                )

            files = sorted(sku_dir.glob("*.jpg"))
            stale_exists_after_download = stale_path.exists()

        self.assertEqual(count, 8)
        self.assertEqual(len(files), 8)
        self.assertFalse(stale_exists_after_download)


if __name__ == "__main__":
    unittest.main()
