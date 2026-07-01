import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from automations.sku_reset import SKUResetService
from automations.supplier_profile import SupplierProfile


def make_profile(
    root: Path,
    key: str,
    products_folder: str,
    product_aggregate_filename: str = "all.csv",
) -> SupplierProfile:
    base = root / key
    return SupplierProfile(
        key=key,
        name_prefix=key.title(),
        manufacturer_title=key.title(),
        products_path=base / products_folder,
        translation_mapping_path=base / "translate.txt",
        input_csv_path=base / "input.csv",
        debug_log_path=base / "logs" / "error_debug.txt",
        translation_errors_path=base / "logs" / "translation_errors.log",
        done_path=base / "done.txt",
        failed_path=base / "failed.txt",
        product_aggregate_filename=product_aggregate_filename,
    )


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class SKUResetServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_cwd = Path.cwd()
        self.addCleanup(self.tempdir.cleanup)
        self.addCleanup(lambda: os.chdir(self.previous_cwd))
        os.chdir(self.root)

        self.profuomo = make_profile(self.root, "profuomo", "products")
        self.venti = make_profile(
            self.root,
            "casamoda_venti",
            "products",
            product_aggregate_filename="venti_all.csv",
        )

        self.profile_patch = patch.multiple(
            "automations.sku_reset",
            PROFUOMO_PROFILE=self.profuomo,
            CASAMODA_VENTI_PROFILE=self.venti,
            create=True,
        )
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)

    def tearDown(self):
        pass

    def test_venti_reset_removes_casamoda_rows_state_and_images_only(self):
        write_csv(
            self.venti.products_path / "shirts.csv",
            [
                {"sku": "001410-000", "name": "parent"},
                {"sku": "001410-000-39", "name": "size"},
                {"sku": "001410-001", "name": "other colour"},
            ],
        )
        write_csv(
            self.venti.products_path / "venti_all.csv",
            [
                {"sku": "001410-000", "name": "parent"},
                {"sku": "001410-000-39", "name": "size"},
                {"sku": "001410-001", "name": "other colour"},
            ],
        )
        write_lines(self.venti.done_path, ["001410-000", "001410-001"])
        write_lines(self.venti.failed_path, ["001410-000,error", "001410-001,error"])
        (self.venti.products_path / "001410-000").mkdir(parents=True)

        write_csv(
            self.profuomo.products_path / "shirts.csv",
            [{"sku": "001410-000", "name": "must stay"}],
        )
        write_lines(self.profuomo.done_path, ["001410-000"])
        (self.profuomo.products_path / "001410-000").mkdir(parents=True)

        write_csv(
            self.root / "magento_products.csv",
            [
                {"SKU": "001410-000", "name": "parent"},
                {"SKU": "001410-000-39", "name": "size"},
                {"SKU": "001410-001", "name": "other colour"},
            ],
        )

        result = SKUResetService.reset_sku_everywhere("001410-000", supplier="venti")

        self.assertEqual(result["error"], "")
        venti_rows = pd.read_csv(self.venti.products_path / "shirts.csv", dtype=str)
        self.assertEqual(venti_rows["sku"].tolist(), ["001410-001"])
        rebuilt_all = pd.read_csv(self.venti.products_path / "venti_all.csv", dtype=str)
        self.assertEqual(rebuilt_all["sku"].tolist(), ["001410-001"])
        self.assertFalse((self.venti.products_path / "all.csv").exists())
        self.assertEqual(
            self.venti.done_path.read_text(encoding="utf-8").splitlines(),
            ["001410-001"],
        )
        self.assertEqual(
            self.venti.failed_path.read_text(encoding="utf-8").splitlines(),
            ["001410-001,error"],
        )
        self.assertFalse((self.venti.products_path / "001410-000").exists())

        profuomo_rows = pd.read_csv(self.profuomo.products_path / "shirts.csv", dtype=str)
        self.assertEqual(profuomo_rows["sku"].tolist(), ["001410-000"])
        self.assertTrue((self.profuomo.products_path / "001410-000").exists())
        self.assertEqual(
            self.profuomo.done_path.read_text(encoding="utf-8").splitlines(),
            ["001410-000"],
        )

        magento_rows = pd.read_csv(self.root / "magento_products.csv", dtype=str)
        self.assertEqual(magento_rows["SKU"].tolist(), ["001410-001"])

    def test_auto_reset_removes_matching_state_from_both_supplier_profiles(self):
        write_csv(self.profuomo.products_path / "shirts.csv", [{"sku": "DUPSKU"}])
        write_csv(self.venti.products_path / "shirts.csv", [{"sku": "DUPSKU"}])
        write_lines(self.profuomo.done_path, ["DUPSKU"])
        write_lines(self.venti.done_path, ["DUPSKU"])
        (self.profuomo.products_path / "DUPSKU").mkdir(parents=True)
        (self.venti.products_path / "DUPSKU").mkdir(parents=True)

        result = SKUResetService.reset_sku_everywhere("DUPSKU", supplier="auto")

        self.assertEqual(result["error"], "")
        self.assertTrue(pd.read_csv(self.profuomo.products_path / "shirts.csv").empty)
        self.assertTrue(pd.read_csv(self.venti.products_path / "shirts.csv").empty)
        self.assertFalse((self.profuomo.products_path / "DUPSKU").exists())
        self.assertFalse((self.venti.products_path / "DUPSKU").exists())
        self.assertEqual(self.profuomo.done_path.read_text(encoding="utf-8"), "")
        self.assertEqual(self.venti.done_path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
