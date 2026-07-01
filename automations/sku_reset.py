from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Callable

import pandas as pd

from automations.supplier_profile import CASAMODA_VENTI_PROFILE, PROFUOMO_PROFILE, SupplierProfile


@dataclass
class FileChange:
    path: str
    removed: int


class SKUResetService:
    PRODUCT_AGGREGATE_EXCLUDES = {"all.csv", "input.csv"}
    SHARED_TEXT_PATHS = (
        Path("input.csv"),
        Path("urlerror.log"),
        Path("notfound.txt"),
        Path("notfound_copy.txt"),
        Path("translation_errors.log"),
    )

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
    def _extract_sku_key(cls, value: object) -> str:
        normalized = cls._normalize_cell_value(value)
        if not normalized:
            return ""
        return normalized.split(",", 1)[0].strip()

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
        sku_keys = df[matched_col].map(cls._extract_sku_key)
        drop_mask = sku_keys.eq(sku) | sku_keys.str.startswith(f"{sku}-")
        filtered = df[~drop_mask]
        removed = before - len(filtered)
        if removed > 0:
            filtered.to_csv(path, index=False)
        return removed

    @classmethod
    def _profiles_for_supplier(cls, supplier: str | None) -> list[SupplierProfile]:
        supplier_key = str(supplier or "auto").strip().lower()
        if supplier_key in {"", "auto", "all"}:
            return [PROFUOMO_PROFILE, CASAMODA_VENTI_PROFILE]
        if supplier_key == "profuomo":
            return [PROFUOMO_PROFILE]
        if supplier_key in {"venti", "casamoda", "casamoda_venti"}:
            return [CASAMODA_VENTI_PROFILE]
        raise ValueError(
            "Unknown supplier. Choose Auto, Profuomo, or VENTI."
        )

    @classmethod
    def _profile_text_paths(cls, profile: SupplierProfile) -> tuple[Path, ...]:
        return (
            profile.input_csv_path,
            profile.done_path,
            profile.failed_path,
            profile.translation_errors_path,
        )

    @staticmethod
    def _unique_paths(paths: list[Path]) -> list[Path]:
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    @classmethod
    def _line_matches_sku(cls, line: str, sku: str) -> bool:
        normalized = cls._normalize_cell_value(line)
        return (
            normalized == sku
            or normalized.startswith(f"{sku},")
            or normalized.startswith(f"{sku}-")
            or normalized.startswith(f"{sku};")
        )

    @classmethod
    def _iter_product_csvs_for_aggregate(cls, products_dir: Path):
        if not products_dir.exists() or not products_dir.is_dir():
            return
        for path in sorted(
            candidate
            for candidate in products_dir.iterdir()
            if candidate.is_file()
            and candidate.suffix.lower() == ".csv"
            and candidate.name.lower() not in cls.PRODUCT_AGGREGATE_EXCLUDES
        ):
            yield path

    @classmethod
    def _rebuild_all_csv(cls, products_dir: Path) -> bool:
        all_path = products_dir / "all.csv"
        frames: list[pd.DataFrame] = []

        for csv_path in cls._iter_product_csvs_for_aggregate(products_dir):
            try:
                df = pd.read_csv(csv_path, dtype=str)
            except Exception:
                continue
            if df.empty or "sku" not in df.columns:
                continue
            frames.append(df)

        if not frames:
            if all_path.exists():
                try:
                    existing = pd.read_csv(all_path, dtype=str)
                    existing.head(0).to_csv(all_path, index=False)
                except Exception:
                    all_path.write_text("sku\n", encoding="utf-8")
                return True
            return False

        merged = pd.concat(frames, ignore_index=True, sort=False)
        normalized = merged["sku"].map(cls._extract_sku_key)
        merged = merged.loc[normalized.ne("")]
        merged = merged.assign(_normalized_sku=normalized.loc[merged.index])
        merged = merged.drop_duplicates(subset=["_normalized_sku"], keep="first")
        merged = merged.drop(columns=["_normalized_sku"]).reset_index(drop=True)
        merged.to_csv(all_path, index=False)
        return True

    @classmethod
    def _reset_one_sku(
        cls,
        sku: str,
        profiles: list[SupplierProfile],
    ) -> dict[str, object]:
        if not sku:
            return {"message": "", "error": "SKU is required", "sku": ""}

        changes: list[FileChange] = []
        warnings: list[str] = []

        # 1) Remove SKU rows from supplier product CSVs (all/category files)
        for profile in profiles:
            products_dir = profile.products_path
            if products_dir.exists() and products_dir.is_dir():
                for csv_path in sorted(
                    path
                    for path in products_dir.iterdir()
                    if path.is_file() and path.suffix.lower() == ".csv"
                ):
                    removed = cls._remove_rows_by_columns(csv_path, sku, ("sku",))
                    if removed:
                        changes.append(FileChange(str(csv_path), removed))
                try:
                    cls._rebuild_all_csv(products_dir)
                except Exception as exc:
                    warnings.append(
                        f"Could not rebuild {Path(products_dir, 'all.csv')}: {exc}"
                    )

        # 2) Remove SKU rows from Magento/export and stock CSV files in project root
        root_csv_columns = ("SKU", "sku", "ArtikelNr", "id")
        for csv_path in sorted(
            path for path in Path(".").iterdir() if path.is_file() and path.suffix.lower() == ".csv"
        ):
            removed = cls._remove_rows_by_columns(csv_path, sku, root_csv_columns)
            if removed:
                changes.append(FileChange(str(csv_path), removed))

        # 3) Remove SKU from line-based state files
        text_paths = list(cls.SHARED_TEXT_PATHS)
        for profile in profiles:
            text_paths.extend(cls._profile_text_paths(profile))
        for txt_path in cls._unique_paths(text_paths):
            try:
                removed = cls._remove_lines(
                    txt_path,
                    lambda line, s=sku: cls._line_matches_sku(line, s),
                )
                if removed:
                    changes.append(FileChange(str(txt_path), removed))
            except Exception as exc:
                warnings.append(f"Could not process {txt_path}: {exc}")

        # 4) Remove SKU image folder
        deleted_folders: list[str] = []
        for profile in profiles:
            products_dir = profile.products_path
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
    def reset_sku_everywhere(
        cls,
        raw_sku: str,
        supplier: str | None = "auto",
    ) -> dict[str, object]:
        skus = cls._parse_skus(raw_sku)
        if not skus:
            return {"message": "", "error": "SKU is required"}

        try:
            profiles = cls._profiles_for_supplier(supplier)
        except ValueError as exc:
            return {"message": "", "error": str(exc)}

        if len(skus) == 1:
            return cls._reset_one_sku(skus[0], profiles)

        results = [cls._reset_one_sku(sku, profiles) for sku in skus]
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
