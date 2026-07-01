from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config import BASE_DIR, PRODUCTS_PATH


@dataclass(frozen=True)
class SupplierProfile:
    key: str
    name_prefix: str
    manufacturer_title: str
    products_path: Path
    translation_mapping_path: Path
    input_csv_path: Path
    debug_log_path: Path
    translation_errors_path: Path
    done_path: Path
    failed_path: Path
    product_aggregate_filename: str = "all.csv"
    default_weight: str = "0.5"
    default_chest_pocket: str = "Zonder borstzak"
    default_size_table: str = ""
    size_tables: dict[tuple[str, str], str] = field(default_factory=dict)
    sleeve_size_tables: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def ensure_directories(self) -> None:
        self.products_path.mkdir(parents=True, exist_ok=True)
        self.input_csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.translation_errors_path.parent.mkdir(parents=True, exist_ok=True)
        self.done_path.parent.mkdir(parents=True, exist_ok=True)
        self.failed_path.parent.mkdir(parents=True, exist_ok=True)

    def size_table_for(
        self,
        source_category: str,
        fit: str,
        mapped_category: str,
        sleeve: str = "",
    ) -> str:
        category_key = str(mapped_category or source_category or "").strip()
        fit_key = str(fit or "").strip().upper()
        sleeve_key = str(sleeve or "").strip().upper()
        return (
            self.sleeve_size_tables.get((category_key, fit_key, sleeve_key))
            or self.sleeve_size_tables.get((category_key, "", sleeve_key))
            or self.size_tables.get((category_key, fit_key))
            or self.size_tables.get((category_key, ""))
            or self.default_size_table
        )


PROFUOMO_PROFILE = SupplierProfile(
    key="profuomo",
    name_prefix="Profuomo",
    manufacturer_title="Profuomo",
    products_path=Path(PRODUCTS_PATH),
    translation_mapping_path=Path(BASE_DIR) / "translate_mapping.txt",
    input_csv_path=Path(PRODUCTS_PATH) / "input.csv",
    debug_log_path=Path("error_debug.txt"),
    translation_errors_path=Path("translation_errors.log"),
    done_path=Path(PRODUCTS_PATH) / "done.txt",
    failed_path=Path(PRODUCTS_PATH) / "failed.txt",
    default_size_table="Profuomo slim fit overhemden",
    size_tables={
        ("Truien", "SLIM FIT"): "Profuomo slim fit truien",
        ("Truien", "NORMAL FIT"): "Profuomo normal fit truien",
        ("Truien", "REGULAR FIT"): "Profuomo regular fit truien",
        ("Truien", ""): "Profuomo slim fit truien",
        ("Overhemden", "RELAXED FIT"): "Profuomo relaxed fit overhemden",
        ("Overhemden", "REGULAR FIT"): "Profuomo regular fit overhemden",
        ("Overhemden", "SUPER SLIM FIT"): "Profuomo super slim fit overhemden",
        ("Overhemden", ""): "Profuomo slim fit overhemden",
        ("Overshirts", ""): "Profuomo overshirt normal fit",
        ("Polo's", ""): "Profuomo polo normal fit",
    },
)

CASAMODA_VENTI_PROFILE = SupplierProfile(
    key="casamoda_venti",
    name_prefix="Venti",
    manufacturer_title="VENTI",
    products_path=Path(BASE_DIR) / "Casamoda" / "products",
    translation_mapping_path=Path(BASE_DIR) / "Casamoda" / "translate_casamoda.txt",
    input_csv_path=Path(BASE_DIR) / "Casamoda" / "input.csv",
    debug_log_path=Path(BASE_DIR) / "Casamoda" / "logs" / "error_debug.txt",
    translation_errors_path=Path(BASE_DIR) / "Casamoda" / "logs" / "translation_errors.log",
    done_path=Path(BASE_DIR) / "Casamoda" / "done.txt",
    failed_path=Path(BASE_DIR) / "Casamoda" / "failed.txt",
    product_aggregate_filename="venti_all.csv",
    default_size_table="Venti modern fit overhemden",
    size_tables={
        ("Overhemden", "MODERN FIT"): "Venti modern fit overhemden",
        ("Overhemden", "BODY FIT"): "Venti body fit overhemden",
        ("Overhemden", "COMFORT FIT"): "Casa Moda comfort fit",
        ("Overhemden", ""): "Venti modern fit overhemden",
    },
    sleeve_size_tables={
        (
            "Overhemden",
            "MODERN FIT",
            "EXTRA LANG 69CM",
        ): "Venti modern fit overhemden extra lange mouwen 69 cm",
        (
            "Overhemden",
            "MODERN FIT",
            "EXTRA LANGE MOUW 69 CM",
        ): "Venti modern fit overhemden extra lange mouwen 69 cm",
        (
            "Overhemden",
            "MODERN FIT",
            "EXTRA LANG 72CM",
        ): "Venti modern fit overhemden extra lange mouwen 72 cm",
        (
            "Overhemden",
            "MODERN FIT",
            "EXTRA LANGE MOUW 72 CM",
        ): "Venti modern fit overhemden extra lange mouwen 72 cm",
        (
            "Overhemden",
            "BODY FIT",
            "EXTRA LANG 69CM",
        ): "Venti body fit overhemden extra lange mouwen 69 cm",
        (
            "Overhemden",
            "BODY FIT",
            "EXTRA LANGE MOUW 69 CM",
        ): "Venti body fit overhemden extra lange mouwen 69 cm",
        (
            "Overhemden",
            "BODY FIT",
            "EXTRA LANG 72CM",
        ): "Venti body fit overhemden extra lange mouwen 72 cm",
        (
            "Overhemden",
            "BODY FIT",
            "EXTRA LANGE MOUW 72 CM",
        ): "Venti body fit overhemden extra lange mouwen 72 cm",
    },
)


def get_supplier_profile(supplier: str | None = None) -> SupplierProfile:
    supplier_key = str(supplier or "profuomo").strip().lower()
    if supplier_key in {"casamoda", "venti", "casamoda_venti"}:
        return CASAMODA_VENTI_PROFILE
    return PROFUOMO_PROFILE
