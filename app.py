import pandas as pd
import os
from dotenv import load_dotenv
import imaplib
import email
import datetime
import sys

# Load credentials from .env file
if os.path.exists(".env"):
    load_dotenv(".env")
from io import StringIO

from automations.magento import MagentoFiller, MagentoUploader
from automations.sku_reset import SKUResetService
from config import (
    EMAIL_ADDRESS,
    PASSWORD,
    PRODUCTS_PATH,
    TEST,
)
from automations.profuomo import ProfuomoDownloader, ProfuomoScraper

if getattr(sys, "frozen", False):
    # we are running in a bundle
    sys.stdout = mystdout = StringIO()
    sys.stderr = mystderr = StringIO()

import eel


eel.init("web")


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
def register_products(csv_path: str):
    path = csv_path
    if not csv_path:
        path = None
    return MagentoFiller.register_products(csv_path=path, test=TEST)


@eel.expose
def reset_sku(sku: str):
    return SKUResetService.reset_sku_everywhere(sku)


@eel.expose
def get_csvs() -> list[str]:
    try:
        return [
            os.path.join(PRODUCTS_PATH, csv)
            for csv in os.listdir(PRODUCTS_PATH)
            if csv.endswith(".csv")
        ]
    except Exception:
        return []


@eel.expose
def health():
    return True


def autoimport():
    with open("autoimport.txt", "r") as f:
        urls = f.readlines()
    for url in urls:
        profuomo_scraper(url.strip())
    register_products(csv_path=os.path.join(PRODUCTS_PATH, "all.csv"))


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

    else:
        eel.start("index.html", size=(420, 520))
