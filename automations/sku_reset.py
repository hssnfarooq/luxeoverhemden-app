from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Callable

import pandas as pd

from config import PRODUCTS_PATH


@dataclass
class FileChange:
    path: str
    removed: int


class SKUResetService:
    @staticmethod
    def _normalize_sku(sku: str) -> str:
        return (sku or "").strip().upper()

    @classmethod
    def _remove_lines(cls, path: Path, matcher: Callable[[str], bool]) -> int:
        if not path.exists() or not path.is_file():
            return 0
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if not text:
            return 0
        original_lines = text.splitlines()
        kept_lines = [line for line in original_lines if not matcher(line)]
        removed = len(original_lines) - len(kept_lines)
        if removed > 0:
            path.write_text(
                "\n".join(kept_lines).rstrip() + ("\n" if kept_lines else ""),
                encoding="utf-8",
            )
        return removed

    @classmethod
    def _remove_rows_by_columns(
        cls,
        path: Path,
        sku: str,
        candidate_columns: tuple[str, ...],
    ) -> int:
        if not path.exists() or not path.is_file():
            return 0
        try:
            df = pd.read_csv(path, dtype=str)
        except Exception:
            return cls._remove_lines(
                path,
                lambda line, s=sku: line.split(",")[0].strip().strip('"').upper() == s,
            )
        if df.empty:
            return 0

        lookup = {column.strip().lower(): column for column in df.columns}
        matched_col = None
        for col in candidate_columns:
            if col.lower() in lookup:
                matched_col = lookup[col.lower()]
                break
        if not matched_col:
            return 0

        before = len(df)
        normalized = df[matched_col].fillna("").astype(str).str.strip().str.upper()
        filtered = df[normalized != sku]
        removed = before - len(filtered)
        if removed > 0:
            filtered.to_csv(path, index=False)
        return removed

    @classmethod
    def reset_sku_everywhere(cls, raw_sku: str) -> dict[str, object]:
        sku = cls._normalize_sku(raw_sku)
        if not sku:
            return {"message": "", "error": "SKU is required"}

        changes: list[FileChange] = []
        warnings: list[str] = []

        # 1) Remove SKU rows from product CSVs (all/category files)
        products_dir = Path(PRODUCTS_PATH)
        if products_dir.exists() and products_dir.is_dir():
            for csv_path in sorted(
                path
                for path in products_dir.iterdir()
                if path.is_file() and path.suffix.lower() == ".csv"
            ):
                removed = cls._remove_rows_by_columns(csv_path, sku, ("sku",))
                if removed:
                    changes.append(FileChange(str(csv_path), removed))

        # 2) Remove SKU rows from Magento/export and stock CSV files in project root
        root_csv_columns = ("SKU", "sku", "ArtikelNr", "id")
        for csv_path in sorted(
            path for path in Path(".").iterdir() if path.is_file() and path.suffix.lower() == ".csv"
        ):
            removed = cls._remove_rows_by_columns(csv_path, sku, root_csv_columns)
            if removed:
                changes.append(FileChange(str(csv_path), removed))

        # 3) Remove SKU from line-based state files
        text_paths = [
            Path("input.csv"),
            Path(PRODUCTS_PATH, "input.csv"),
            Path(PRODUCTS_PATH, "done.txt"),
            Path(PRODUCTS_PATH, "failed.txt"),
            Path("urlerror.log"),
            Path("notfound.txt"),
            Path("notfound_copy.txt"),
            Path("translation_errors.log"),
        ]
        for txt_path in text_paths:
            try:
                removed = cls._remove_lines(
                    txt_path,
                    lambda line, s=sku: line.strip().upper() == s
                    or line.strip().upper().startswith(f"{s},"),
                )
                if removed:
                    changes.append(FileChange(str(txt_path), removed))
            except Exception as exc:
                warnings.append(f"Could not process {txt_path}: {exc}")

        # 4) Remove SKU image folder
        deleted_folders: list[str] = []
        if products_dir.exists() and products_dir.is_dir():
            for folder in (
                path
                for path in products_dir.iterdir()
                if path.is_dir() and path.name.upper() == sku
            ):
                shutil.rmtree(folder, ignore_errors=True)
                deleted_folders.append(str(folder))

        if not changes and not deleted_folders:
            return {
                "message": f"No references found for SKU {sku}.",
                "error": "",
                "sku": sku,
                "changes": [],
                "deleted_folders": [],
                "warnings": warnings,
            }

        return {
            "message": f"SKU {sku} removed from local state. Run scrape/upload again.",
            "error": "",
            "sku": sku,
            "changes": [change.__dict__ for change in changes],
            "deleted_folders": deleted_folders,
            "warnings": warnings,
        }
