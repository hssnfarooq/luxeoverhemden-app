from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
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
    def _parse_skus(cls, raw_skus: str) -> list[str]:
        if not raw_skus:
            return []
        parts = re.split(r"[\s,;]+", raw_skus.strip())
        unique: list[str] = []
        seen: set[str] = set()
        for part in parts:
            sku = cls._normalize_sku(part)
            if sku and sku not in seen:
                seen.add(sku)
                unique.append(sku)
        return unique

    @staticmethod
    def _normalize_cell_value(value: object) -> str:
        text = "" if value is None else str(value)
        return (
            text.replace("\ufeff", "")
            .replace('"', "")
            .replace("'", "")
            .strip()
            .upper()
        )

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
        normalized = df[matched_col].map(cls._normalize_cell_value)
        drop_mask = normalized.eq(sku) | normalized.str.startswith(f"{sku}-")
        filtered = df[~drop_mask]
        removed = before - len(filtered)
        if removed > 0:
            filtered.to_csv(path, index=False)
        return removed

    @classmethod
    def _reset_one_sku(cls, sku: str) -> dict[str, object]:
        if not sku:
            return {"message": "", "error": "SKU is required", "sku": ""}

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

    @classmethod
    def reset_sku_everywhere(cls, raw_sku: str) -> dict[str, object]:
        skus = cls._parse_skus(raw_sku)
        if not skus:
            return {"message": "", "error": "SKU is required"}

        if len(skus) == 1:
            return cls._reset_one_sku(skus[0])

        results = [cls._reset_one_sku(sku) for sku in skus]
        total_changes = sum(len(result.get("changes", [])) for result in results)
        total_deleted_folders = sum(len(result.get("deleted_folders", [])) for result in results)
        warnings: list[str] = []
        for result in results:
            warnings.extend(result.get("warnings", []))

        return {
            "message": (
                f"Processed {len(skus)} SKUs. "
                f"Updated files: {total_changes}, deleted image folders: {total_deleted_folders}."
            ),
            "error": "",
            "skus": skus,
            "results": results,
            "warnings": warnings,
        }
