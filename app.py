import pandas as pd
import os
from dotenv import load_dotenv
import imaplib
import email
import datetime
import sys
import threading
from pathlib import Path

# Load credentials from .env file
if os.path.exists(".env"):
    load_dotenv(".env")
from io import StringIO

from automations.magento import MagentoFiller, MagentoUploader
from automations.sku_reset import SKUResetService
from config import (
    CASAMODA_PATH,
    EMAIL_ADDRESS,
    PASSWORD,
    PRODUCTS_PATH,
    TEST,
)
from automations.casamoda import CasamodaScraper
from automations.profuomo import ProfuomoDownloader, ProfuomoScraper

if getattr(sys, "frozen", False):
    # we are running in a bundle
    sys.stdout = mystdout = StringIO()
    sys.stderr = mystderr = StringIO()

import eel


eel.init("web")

VENTI_SCRAPE_LOCK = threading.Lock()
VENTI_SCRAPE_STATUS: dict[str, str | bool | int] = {
    "running": False,
    "message": "",
    "error": "",
    "progress": "Idle",
    "products": 0,
    "unknown_prices": 0,
    "categories": 0,
    "csv_path": "",
    "all_csv_path": "",
}


def _set_venti_scrape_status(**updates):
    with VENTI_SCRAPE_LOCK:
        VENTI_SCRAPE_STATUS.update(updates)
        return dict(VENTI_SCRAPE_STATUS)


def _get_venti_scrape_status():
    with VENTI_SCRAPE_LOCK:
        return dict(VENTI_SCRAPE_STATUS)


def _run_venti_scrape(url: str):
    def progress(message: str):
        _set_venti_scrape_status(progress=message)

    try:
        status = CasamodaScraper.scrape_venti(
            url or None,
            progress_callback=progress,
        )
        _set_venti_scrape_status(
            running=False,
            message=str(status.get("message", "")),
            error=str(status.get("error", "")),
            progress="Finished",
            products=int(status.get("products", 0)),
            unknown_prices=int(status.get("unknown_prices", 0)),
            categories=int(status.get("categories", 1 if status.get("csv_path") else 0)),
            csv_path=str(status.get("csv_path", "")),
            all_csv_path=str(status.get("all_csv_path", "")),
        )
    except Exception as ex:
        _set_venti_scrape_status(
            running=False,
            message="",
            error=str(ex),
            progress="Failed",
        )


def _latest_venti_products_csv() -> str:
    products_dir = Path(CASAMODA_PATH) / "products"
    candidates = []
    all_csv = products_dir / "all.csv"
    if all_csv.exists():
        candidates.append(all_csv)
    candidates.extend(products_dir.glob("all_merged_*.csv"))
    if not candidates:
        return str(all_csv)
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


@eel.expose
def download():
    status: dict[str, str | None] = {"message": None, "error": None}
    saved_file = False
    # if file exists, delete it
    if os.path.exists("CM Lagerbestand.xlsx"):
        os.remove("CM Lagerbestand.xlsx")

    try:
        # Load credentials from .env file
        load_dotenv(".env")

        # Connect to Gmail using IMAP
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(EMAIL_ADDRESS, PASSWORD)
        imap.select("inbox")

        # Search for emails from "info@test.com" with attachments
        search_query = (
            '(FROM "bo@casamoda.com" SUBJECT "CM Lagerbestand" SINCE "'
            + (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime(
                "%d-%b-%Y"
            )
            + '")'
        )
        result, data = imap.search(None, search_query)
        latest_email_id = data[0].split()[-1]
        email_found = len(data[0].split()) > 0
        # Get the latest email and extract the attachment
        result, data = imap.fetch(latest_email_id, "(RFC822)")
        email_message = email.message_from_bytes(data[0][1])  # type: ignore
        for part in email_message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition") is None:
                continue
            filename = part.get_filename()

            if filename is not None and filename == "CM Lagerbestand.xlsx":
                with open(filename, "wb") as f:
                    f.write(part.get_payload(decode=True))  # type: ignore
                    saved_file = True
        if not email_found:
            status["error"] = "No email found"
        if saved_file:
            status["message"] = "File downloaded successfully"
        else:
            status["error"] = "File not found"
        # Close the IMAP connection
        imap.close()
        imap.logout()
    except Exception as ex:
        template = "er is een {0} opgetreden: {1!r}"
        status["error"] = template.format(type(ex).__name__, ex.args)
    finally:
        return status


@eel.expose
def merge(filename="CM Lagerbestand.xlsx"):
    status: dict[str, str | None] = {"message": None, "error": None}
    try:
        # if file exists, delete it
        if os.path.exists("import_file.csv"):
            os.remove("import_file.csv")

        # read the Excel file into a pandas dataframe
        excel_file = pd.read_excel(filename, sheet_name=[0, 1], dtype=str)

        # select the first tab and rename the columns to match the desired output
        tab1_df = excel_file[0][
            ["ArtikelNr", "Fb", "Grösse", "GTIN/EAN", "Verfügbare Menge"]
        ]
        tab1_df.columns = ["ArtikelNr", "Fb", "Größe", "GTIN/EAN", "quantity"]

        # select the second tab and rename the columns to match the desired output
        tab2_df = excel_file[1][["ArtikelNr", "Fb", "Grösse", "GTIN/EAN", "quantity"]]
        tab2_df.columns = ["ArtikelNr", "Fb", "Größe", "GTIN/EAN", "quantity"]

        # combine the first two tabs into one dataframe and keep only the first 5 columns
        combined_df = pd.concat([tab1_df, tab2_df])
        combined_df = combined_df.iloc[:, :5]

        # write the new dataframe to a new csv file with this as a example: "ArtikelNr","Fb","Größe","GTIN/EAN","quantity"
        combined_df.to_csv(
            "import_file.csv",
            index=False,
            header=True,
            sep=",",
            quotechar='"',
            quoting=1,
        )
        status["message"] = "File split successfully"
    except Exception as ex:
        template = "er is een {0} opgetreden: {1!r}"
        status["error"] = template.format(type(ex).__name__, ex.args)
    finally:
        return status


@eel.expose
def upload(cmlagerbestand=False, profuomo=False, headless=False):
    return MagentoUploader.upload(
        cmlagerbestand=cmlagerbestand, profuomo=profuomo, headless=headless
    )


@eel.expose
def profuomo(headless=False):
    return ProfuomoDownloader.download_profuomo(headless)


@eel.expose
def profuomo_scraper(url: str):
    return ProfuomoScraper.scrape_profuomo(url)


@eel.expose
def venti_scraper(url: str = ""):
    current_status = _get_venti_scrape_status()
    if current_status.get("running"):
        return current_status

    _set_venti_scrape_status(
        running=True,
        message="VENTI scrape started. No browser will open; this runs in the background.",
        error="",
        progress="Starting...",
        products=0,
        unknown_prices=0,
        categories=0,
        csv_path="",
        all_csv_path="",
    )
    thread = threading.Thread(target=_run_venti_scrape, args=(url,), daemon=True)
    thread.start()
    return _get_venti_scrape_status()


@eel.expose
def venti_scrape_status():
    return _get_venti_scrape_status()


@eel.expose
def register_products(csv_path: str, supplier: str = "profuomo"):
    path = csv_path
    if not csv_path:
        path = None
    return MagentoFiller.register_products(csv_path=path, test=TEST, supplier=supplier)


@eel.expose
def register_venti_products(csv_path: str = ""):
    path = csv_path or _latest_venti_products_csv()
    return MagentoFiller.register_products(csv_path=path, test=TEST, supplier="venti")


@eel.expose
def reset_sku(sku: str):
    return SKUResetService.reset_sku_everywhere(sku)


@eel.expose
def get_csvs() -> list[str]:
    csvs: list[str] = []
    for folder in (PRODUCTS_PATH, os.path.join(CASAMODA_PATH, "products")):
        try:
            csvs.extend(
                os.path.join(folder, csv)
                for csv in os.listdir(folder)
                if csv.endswith(".csv")
            )
        except Exception:
            continue
    return sorted(csvs, key=lambda path: os.path.getmtime(path), reverse=True)


@eel.expose
def health():
    return True


def autoimport():
    with open("autoimport.txt", "r") as f:
        urls = f.readlines()
    for url in urls:
        profuomo_scraper(url.strip())
    register_products(csv_path=os.path.join(PRODUCTS_PATH, "all.csv"))


def venti_autoimport(url: str = ""):
    scrape_status = CasamodaScraper.scrape_venti(
        url or None,
        progress_callback=print,
    )
    if scrape_status.get("error"):
        return scrape_status
    return register_venti_products(os.path.join(CASAMODA_PATH, "products", "all.csv"))


if __name__ == "__main__":
    # check if app is executed with the 'cron' argument
    if len(sys.argv) > 1:
        match sys.argv[1]:
            case "casamoda":
                download()
                merge()
                upload(True, False, headless=True)
            case "profuomo":
                profuomo(headless=True)
                upload(False, True, headless=True)
            case "autoimport":
                autoimport()
            case "venti_scrape":
                print(
                    CasamodaScraper.scrape_venti(
                        sys.argv[2] if len(sys.argv) > 2 else None,
                        progress_callback=print,
                    )
                )
            case "venti_register":
                print(
                    register_venti_products(
                        sys.argv[2] if len(sys.argv) > 2 else ""
                    )
                )
            case "venti_autoimport":
                print(venti_autoimport(sys.argv[2] if len(sys.argv) > 2 else ""))

    else:
        eel.start("index.html", size=(420, 520))
