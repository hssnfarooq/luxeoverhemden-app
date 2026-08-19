import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automations.profuomo import ProfuomoScraper
from automations.supplier_profile import PROFUOMO_PROFILE
from config import BASE_DIR


class ProfuomoScraperSafetyTests(unittest.TestCase):
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
