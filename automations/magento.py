from pathlib import Path
import ast
import csv
import json
import time
import re
import pandas as pd
import sys
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementClickInterceptedException,
    TimeoutException,
    WebDriverException,
)
from urllib3.exceptions import ReadTimeoutError

from automations.scraper import BaseScraper
from automations.supplier_profile import SupplierProfile, get_supplier_profile
from config import (
    MAGENTO_PASSWORD,
    MAGENTO_TEST_PASSWORD,
    MAGENTO_TEST_USERNAME,
    MAGENTO_USERNAME,
    PRODUCTS_PATH,
    TEST,
)
from automations.openai_service import OpenAIService
from config import OPENAI_API_KEY, OPENAI_MODEL

# Initialize OpenAI service
openai_service = OpenAIService(OPENAI_API_KEY, OPENAI_MODEL)


class ProductFoundException(Exception):
    def __init__(self, message="Product not found"):
        self.message = message
        super().__init__(self.message)

    pass


class Magento(BaseScraper):
    @classmethod
    def magento_login(cls, driver: webdriver.Chrome, test: bool = False):
        url = (
            "https://luxeoverhemden.nl/admin/"
            if not test
            else "https://luxetest.luxeoverhemden.nl/admin/"
        )
        username = MAGENTO_USERNAME if not test else MAGENTO_TEST_USERNAME
        password = MAGENTO_PASSWORD if not test else MAGENTO_TEST_PASSWORD
        driver.implicitly_wait(10)

        # navigate to Magento webshop
        driver.get(url)

        # log in with username and password
        try:
            username_input = driver.find_element(By.ID, "username")
            username_input.send_keys(username)

            password_input = driver.find_element(By.ID, "login")
            password_input.send_keys(password)

            login_button = driver.find_element(
                By.XPATH, '//button/span[text()="Log in"]'
            )
            login_button.click()

            cls.random_wait()
            # /html/body[@id='html-body']/div[@class='modals-wrapper']/aside[@class='modal-popup modal-system-messages _show']/div[@class='modal-inner-wrap']/header[@class='modal-header']/button[@class='action-close']
            try:
                close_buttons = driver.find_elements(
                    By.XPATH,
                    "//div[@class='modals-wrapper']//button[@class='action-close']",
                )
                if len(close_buttons) > 0:
                    close_buttons[0].click()
            except Exception:
                pass
        except Exception as e:
            print(e)
            pass


class MagentoUploader(Magento):
    SUPPLIER_STOCK_URL = "https://luxeoverhemden.nl/admin/supplierstock"
    CRONFLAG_URL = "https://luxeoverhemden.nl/cronflag.php"
    IMPORT_BUTTON_XPATH = '//button[text()="Import"]'
    SUCCESS_MESSAGE_XPATH = '//div[@data-ui-id="messages-message-success"]'
    ERROR_MESSAGE_XPATH = '//div[@data-ui-id="messages-message-error"]'
    ANY_MESSAGE_XPATH = '//div[contains(@data-ui-id,"messages-message-")]'
    IMPORT_WAIT_TIMEOUT_SECONDS = int(os.getenv("MAGENTO_IMPORT_TIMEOUT_SECONDS", "2700"))
    IMPORT_WAIT_POLL_SECONDS = int(os.getenv("MAGENTO_IMPORT_POLL_SECONDS", "5"))
    SELENIUM_COMMAND_TIMEOUT_SECONDS = int(
        os.getenv("SELENIUM_COMMAND_TIMEOUT_SECONDS", "600")
    )
    GATEWAY_TIMEOUT_PATTERNS = (
        "504 gateway time-out",
        "505 gateway time-out",
        "gateway time-out",
        "gateway timeout",
    )

    @classmethod
    def wait_for_import_result(
        cls, driver: webdriver.Chrome, timeout_seconds: int | None = None
    ):
        wait_timeout = timeout_seconds or cls.IMPORT_WAIT_TIMEOUT_SECONDS
        deadline = time.monotonic() + wait_timeout
        poll_seconds = max(1, cls.IMPORT_WAIT_POLL_SECONDS)
        last_message_text = ""
        last_driver_error = ""

        while time.monotonic() < deadline:
            try:
                page_text = driver.page_source.lower()
                if any(p in page_text for p in cls.GATEWAY_TIMEOUT_PATTERNS):
                    raise Exception(
                        "Magento import page returned Gateway Time-out. "
                        "This is a server-side timeout (nginx/apache/php-fpm), "
                        "not a browser idle issue."
                    )

                errors = driver.find_elements(By.XPATH, cls.ERROR_MESSAGE_XPATH)
                if errors:
                    error_text = " | ".join(
                        e.text.strip() for e in errors if e.text and e.text.strip()
                    )
                    if not error_text:
                        error_text = "Unknown Magento error."
                    raise Exception(f"Magento import failed: {error_text}")

                success = driver.find_elements(By.XPATH, cls.SUCCESS_MESSAGE_XPATH)
                if success:
                    return

                messages = driver.find_elements(By.XPATH, cls.ANY_MESSAGE_XPATH)
                message_text = " | ".join(
                    msg.text.strip() for msg in messages if msg.text and msg.text.strip()
                )
                if message_text:
                    last_message_text = message_text

            except ReadTimeoutError as ex:
                # ChromeDriver can time out while Magento import keeps page busy.
                last_driver_error = str(ex)
            except TimeoutException as ex:
                last_driver_error = str(ex)
            except WebDriverException as ex:
                last_driver_error = str(ex)

            time.sleep(poll_seconds)

        timeout_details = []
        if last_message_text:
            timeout_details.append(f"Messages: {last_message_text}")
        if last_driver_error:
            timeout_details.append(f"Last driver error: {last_driver_error}")
        details = " | ".join(timeout_details) if timeout_details else "No details."
        raise TimeoutException(
            f"Timed out after {wait_timeout}s waiting for Magento import result. {details}"
        )

    @classmethod
    def upload_single_file(cls, driver: webdriver.Chrome, filename: str):
        file_path = Path(filename).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        driver.get(cls.SUPPLIER_STOCK_URL)
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "csv")))
        upload_input = driver.find_element(By.ID, "csv")
        upload_input.send_keys(str(file_path))

        import_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, cls.IMPORT_BUTTON_XPATH))
        )
        try:
            import_button.click()
        except ReadTimeoutError:
            # Submit can still be accepted by Magento even when the driver command times out.
            pass
        cls.wait_for_import_result(driver)

    @staticmethod
    def handle_upload_error(driver, ex):
        print("TimeoutException: " + str(ex))
        loading = True
        tries = 0
        while loading:
            try:
                print("loading")
                tries += 1
                loading = (
                    driver.execute_script("return document.readyState") != "complete"
                )
                # if the page contains '<div data-ui-id="messages-message-success">Supplier Stock is Updated.</div>' break the while loop
                try:
                    success_message = driver.find_element(
                        By.XPATH,
                        '//div[@data-ui-id="messages-message-success"]',
                    )
                    if success_message:
                        break
                except NoSuchElementException:
                    if tries > 60:
                        raise Exception("Timeout")
                    time.sleep(10)
                    pass
            except ReadTimeoutError:
                pass
            except Exception as ex:
                raise ex

    @classmethod
    def upload(cls, cmlagerbestand=False, profuomo=False, headless=False):
        status: dict[str, str | None] = {"message": None, "error": None}
        if not cmlagerbestand and not profuomo:
            status["error"] = "No file selected"
            return status
        driver = None
        try:
            options = webdriver.ChromeOptions()
            if headless:
                options.add_argument("headless")
            driver = webdriver.Chrome(options=options)
            try:
                driver.command_executor.set_timeout(cls.SELENIUM_COMMAND_TIMEOUT_SECONDS)
            except Exception:
                pass
            cls.magento_login(driver)
            files_to_upload: list[str] = []
            if cmlagerbestand:
                files_to_upload.append("import_file.csv")
            if profuomo:
                files_to_upload.append("profuomo_products.csv")

            for filename in files_to_upload:
                cls.upload_single_file(driver, filename)

            driver.get(cls.CRONFLAG_URL)
            time.sleep(3)
            status["message"] = "File uploaded successfully"

        except Exception as ex:
            template = "er is een {0} opgetreden: {1!r}"
            status["error"] = template.format(type(ex).__name__, ex.args)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
        return status


class MagentoFiller(Magento):
    PLACEHOLDER_IMAGE_DIMENSIONS = (256, 256)
    ACTIVE_PROFILE: SupplierProfile = get_supplier_profile("profuomo")
    XPATH_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    XPATH_LOWER = "abcdefghijklmnopqrstuvwxyz"
    FIT_OPTION_TITLES = {
        "basic circular knit": "Modern fit",
        "body fit": "Body slim fit",
        "comfort fit": "Comfort fit",
        "modern fit": "Modern fit",
    }
    MISSING_SPEC_FIELDS = [
        "timestamp",
        "sku",
        "field",
        "source_value",
        "mapped_value",
        "target",
        "reason",
    ]

    FORM_MAPPING = {
        "Productnaam": "name:product[name]",
        "sku": "name:product[sku]",
        "ean": "name:product[ean]",
        "rrp": "name:product[price]",
        "fit": "name:product[model]=option:data-title;capitalize",
        "cuff": "name:product[manchet]=option:data-title;dutch",
        "noniron": "option:data-title;constant:EASY CARE;capitalize",
        "quality": "option:data-title[name-'product[materiaal]'];dutch",
        "capsule": "option:data-title[name-'product[capsule]'];capitalize",
        "fabriccomp": "option:data-title;dutch",
        "sustainability": "option:data-title;dutch",
        "design": "option:data-title;dutch",
        "color": "option:data-title;dutch",
        "collar": "name:product[kraag]=option:data-title;dutch",
        "sleeve": "name:product[mouwen]=option:data-title;dutch",
    }
    TRANSLATE_MAPPING: dict[str, str]
    DESCRIPTION_PROMPT = "Maak een productbeschrijving in het Nederlands, in platte tekstformaat, met een limiet van 300 tekens, voor een '{}' met de volgende kenmerken:"
    NEGATIVE_PROMPT = """
Maar benoem in de geschrijving:
- Geen prijs 
- Geen artikelnummer 
- Geen available from datum 
- Geen pasvorm
- Geen maat.

en pas de juiste punctuatie toe, zorg er ook voor dat de hoofdletters correct zijn."""

    FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "sleeve": ("sleeve", "sleeve length", "sleevelength", "mouwlengte"),
        "collar": ("collar", "kraag"),
        "cuff": ("cuff", "manchet"),
        "sustainability": ("sustainability", "iconen", "icons"),
        "noniron": ("noniron", "easy care"),
        "quality": ("quality", "materiaal", "material"),
        "fabriccomp": ("fabriccomp", "fabric composition"),
        "rrp": ("rrp", "rsp", "price"),
    }

    @classmethod
    def configure_supplier(cls, supplier: str | None = None) -> SupplierProfile:
        cls.ACTIVE_PROFILE = get_supplier_profile(supplier)
        cls.ACTIVE_PROFILE.ensure_directories()
        return cls.ACTIVE_PROFILE

    @classmethod
    def profile(cls) -> SupplierProfile:
        return cls.ACTIVE_PROFILE

    @classmethod
    def products_path(cls) -> Path:
        return cls.profile().products_path

    @classmethod
    def debug_log_path(cls) -> Path:
        path = cls.profile().debug_log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def translation_errors_path(cls) -> Path:
        path = cls.profile().translation_errors_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def missing_specs_path(cls) -> Path:
        path = cls.translation_errors_path().parent / "missing_magento_specs.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def reset_missing_specs_report(cls) -> None:
        path = cls.missing_specs_path()
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cls.MISSING_SPEC_FIELDS)
            writer.writeheader()

    @classmethod
    def log_missing_magento_spec(
        cls,
        *,
        sku: object,
        field: object,
        source_value: object,
        mapped_value: object = "",
        target: object = "",
        reason: object = "",
    ) -> None:
        path = cls.missing_specs_path()
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cls.MISSING_SPEC_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "sku": str(sku),
                    "field": str(field),
                    "source_value": str(source_value),
                    "mapped_value": str(mapped_value),
                    "target": str(target),
                    "reason": str(reason),
                }
            )

    @classmethod
    def input_csv_path(cls) -> Path:
        return cls.profile().input_csv_path

    @staticmethod
    def download_current_products(driver: webdriver.Chrome):
        # Wait for the page to load
        time.sleep(2)

        try:
            driver.execute_script("window.scrollTo(0, 0);")
            reset_buttons = driver.find_elements(
                By.CSS_SELECTOR, "[data-action='grid-filter-reset']"
            )
            if reset_buttons:
                # scroll to the top of the page
                # wait until the button wiht data-action="grid-filter-reset" us clickable and click on it
                time.sleep(0.2)
                reset_button = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (
                            By.XPATH,
                            "(//button[@data-action='grid-filter-reset'])[1]",
                        )
                    )
                )
                reset_button.click()
                time.sleep(2)
        except Exception:
            pass

        # Select all products
        select_all_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "(//thead//button[@class='action-multicheck-toggle'])[2]",
                )
            )
        )
        select_all_button.click()

        select_all_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//div[@class='action-multicheck-wrap _active']/ul[@class='action-menu']/li[1]/span[@class='action-menu-item']",
                )
            )
        )
        select_all_option.click()

        # Click on Export dropdown
        export_dropdown = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "(//button[@class='admin__action-dropdown' and @data-bind='toggleCollapsible'])[3]",
                )
            )
        )
        export_dropdown.click()

        # Click on Export button
        export_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//div[@class='admin__action-dropdown-menu admin__data-grid-action-export-menu']//button[@class='action-secondary']",
                )
            )
        )
        export_button.click()

        # Click on Download button
        export_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//footer//span[contains(.,'Export Selected Products')]",
                )
            )
        )
        export_button.click()

        time.sleep(2)

        # Wait for the export to complete (up to 10 minutes)
        WebDriverWait(driver, 1200).until(
            EC.invisibility_of_element_located(
                (
                    By.XPATH,
                    "//div[@class='popup popup-loading']",
                )
            )
        )

        # Get the downloaded file and save it in the current working directory
        download_path = Path("magento_products.csv")
        time.sleep(5)
        
        # Handle file download with better error handling
        print("Handling file download...")
        
        try:
            # Look for the most recent CSV file in current directory
            csv_files = list(Path().glob("*.csv"))
            if csv_files:
                latest_file = max(csv_files, key=lambda path: path.stat().st_ctime)
                print(f"Found latest CSV file: {latest_file}")
                
                # Only rename if it's not already the target file
                if latest_file.name != "magento_products.csv":
                    # Remove existing magento_products.csv if it exists
                    download_path.unlink(missing_ok=True)
                    # Rename the latest file to magento_products.csv
                    latest_file.rename(download_path)
                    print(f"Renamed {latest_file} to {download_path}")
                else:
                    print(f"File {download_path} already exists and is up to date")
            else:
                raise FileNotFoundError("No CSV file found for download")
                
        except Exception as e:
            print(f"Error handling file download: {e}")
            # If running as PyInstaller build, try Downloads folder as fallback
            if getattr(sys, "frozen", False):
                print("Trying Downloads folder as fallback...")
                downloads_path = Path.home() / "Downloads"
                csv_files = list(downloads_path.glob("*.csv"))
                if csv_files:
                    latest_file = max(csv_files, key=lambda path: path.stat().st_ctime)
                    print(f"Found CSV file in Downloads: {latest_file}")
                    download_path.unlink(missing_ok=True)
                    latest_file.rename(download_path)
                    print(f"Renamed {latest_file} to {download_path}")
                else:
                    raise FileNotFoundError("No CSV file found in current directory or Downloads folder")
            else:
                raise e

    @classmethod
    def _get_mapping(cls) -> None:
        try:
            mapping_file = cls.profile().translation_mapping_path
            print(f"Loading translation mapping from: {mapping_file.resolve()}")
            print(f"File exists: {mapping_file.exists()}")
            print(f"Current working directory: {os.getcwd()}")
            print(f"Executable location: {sys.executable}")
            
            with mapping_file.open(encoding="utf-8-sig") as f:
                mapping = {}
                file = f.read()
                try:
                    for line in file.split("\n"):
                        stripped_line = line.strip()
                        if not stripped_line:
                            continue
                        key, separator, value = stripped_line.partition(":")
                        if not separator:
                            continue
                        mapping[key.strip().lower()] = value.strip()
                finally:
                    cls.TRANSLATE_MAPPING = mapping
                    print(f"Successfully loaded {len(mapping)} translation mappings")
        except Exception as e:
            print(f"Error loading translation mapping: {e}")
            cls.TRANSLATE_MAPPING = {}

    @classmethod
    def go_to_form(cls, driver: webdriver.Chrome):
        # Wait for the button with the id 'add_new_product-button' to be clickable and click on it
        add_new_product_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[@aria-label='Add product of type' or @data-ui-id='products-list-add-new-product-button-dropdown']",
                )
            )
        )
        add_new_product_button.click()

        cls.random_wait()

        # Wait for the button with the data-ui-id 'products-list-add-new-product-button-item-configurable' to be clickable and click on it
        configurable_product_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//span[@data-ui-id='products-list-add-new-product-button-item-configurable']",
                )
            )
        )
        configurable_product_button.click()

        cls.random_wait()

    @classmethod
    def go_to_product_catalogue(cls, driver: webdriver.Chrome):
        # Wait for the <li> element with the id 'menu-magento-catalog-catalog' to be clickable and click on the <a> tag inside it
        catalog_li = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "menu-magento-catalog-catalog"))
        )
        # catalog_li.click()
        catalog_a = catalog_li.find_element(By.TAG_NAME, "a")
        catalog_a.click()

        cls.random_wait()

        # Wait for the <li> element with the class 'item-catalog-products' to be clickable and click on the <a> tag inside it
        products_li = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CLASS_NAME, "item-catalog-products"))
        )
        products_a = products_li.find_element(By.TAG_NAME, "a")
        products_a.click()

        cls.random_wait()

    @classmethod
    def gen_description(cls, product: pd.Series, productnaam_nl: str) -> str:
        if productnaam_nl:
            product["name"] = productnaam_nl
        tags = {k: v for k, v in product.items() if v and str(v) != "nan"}
        initialization_prompt = cls.DESCRIPTION_PROMPT.format(product["name"])
        description = openai_service.ask_question(
            initialization_prompt=initialization_prompt,
            question=str(tags) + cls.NEGATIVE_PROMPT,
        )
        if description is None:
            raise NotImplementedError
        if "```" in description:
            description = description.split("```")[1]
        if description.startswith("html"):
            description = description.split("html")[1]
        return description

    @classmethod
    def gen_metadescription(cls, product: pd.Series, productnaam_nl: str) -> str:
        if productnaam_nl:
            product["name"] = productnaam_nl
        tags = {k: v for k, v in product.items() if v and str(v) != "nan"}
        initialization_prompt = cls.DESCRIPTION_PROMPT.format(product["name"]).replace(
            "300", "160"
        )
        description = openai_service.ask_question(
            initialization_prompt=initialization_prompt,
            question=str(tags) + cls.NEGATIVE_PROMPT,
        )
        if description is None:
            raise NotImplementedError
        if "```" in description:
            description = description.split("```")[1]
        if description.startswith("html"):
            description = description.split("html")[1]
        return description

    @classmethod
    def add_description(
        cls, driver: webdriver.Chrome, product: pd.Series, productnaam_nl: str
    ) -> None:
        # Wait for the <span> element with the text 'Content' inside a <strong> with the class 'admin__collapsible-title' to be clickable and click on it
        content_span = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//strong[@class='admin__collapsible-title']//span[text()='Content']",
                )
            )
        )
        content_span.click()
        # Generate the description
        description = cls.gen_description(product, productnaam_nl)

        # Locate the text area with the name 'short_description' and insert the description
        toggle_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "toggleproduct_form_short_description"))
        )
        toggle_button.click()
        short_description_textarea = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "short_description"))
        )

        short_description_textarea.clear()
        short_description_textarea.send_keys(description)

    @classmethod
    def fetch_data(cls, product: pd.Series) -> dict[str, tuple[str, str]]:
        data = {}
        sku = product.get("sku", "UNKNOWN")
        debug_log = cls.debug_log_path()
        
        # Log to debug file
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"\n--- FETCH_DATA for SKU: {sku} ---\n")
            f.write(f"Product data: {dict(product)}\n")
            f.write(f"Category: {product.get('category', 'UNKNOWN')}\n")
            f.write(f"Available translation mappings: {len(cls.TRANSLATE_MAPPING)}\n")
        
        for key, value in cls.FORM_MAPPING.items():
            raw_value = None
            aliases = cls.FIELD_ALIASES.get(key, (key,))
            for alias in aliases:
                if alias in product.index and str(product[alias]) != "nan" and product[alias]:
                    raw_value = product[alias]
                    break

            if (
                raw_value is None
            ):
                # Special handling for overshirts: if collar is empty/nan, use "overshirt"
                if key == "collar" and product["category"] == "Overshirts":
                    raw_value = "overshirt"
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(f"  Special handling for overshirts collar: set to 'overshirt'\n")
                else:
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(f"  Skipping {key}: not in product or is nan/empty\n")
                    continue

            raw_value = cls.normalize_attribute_value(key, raw_value)
            if not str(raw_value).strip():
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"  Skipping {key}: normalized value is empty\n")
                continue
                    
            if "|" in value:
                fields = value.split("|")
            else:
                fields = [value]
                
            for field in fields:
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"  Processing field: {field} for key: {key}\n")
                    f.write(f"    Original value: {raw_value}\n")
                
                if field == "Productnaam":
                    value_text = str(raw_value)
                    profile_prefix = cls.profile().name_prefix
                    if profile_prefix and not value_text.lower().startswith(profile_prefix.lower() + " "):
                        value_text = profile_prefix + " " + value_text
                    value_text = value_text.replace("SC SF ", "")
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(f"    Productnaam processed: {value_text}\n")
                    # Product name should not be translated, use as-is
                    element = cls.get_element(field)
                    data[value_text] = element
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(f"    Added to data: {value_text} -> {element}\n")
                    continue
                    
                k = cls.format_key(field, str(raw_value), product["category"])
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    format_key result: {k}\n")
                
                if k is None or not str(k).strip():
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(f"    SKIPPED: format_key returned empty/None\n")
                    cls.log_missing_magento_spec(
                        sku=sku,
                        field=key,
                        source_value=raw_value,
                        mapped_value=k or "",
                        target=field,
                        reason="missing translation mapping",
                    )
                    continue
                    
                element = cls.get_element(field)
                data[k] = element
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    Added to data: {k} -> {element}\n")
        
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"  Final data dict: {data}\n")
            f.write(f"  Data entries: {len(data)}\n")
        
        return data

    @classmethod
    def get_element(cls, field: str) -> tuple[str, str]:
        field = field.split(";")[0]
        if "=" in field:
            return ("CLICKABLE", field)
        if field.startswith("in["):
            field = "]".join(field.split("]")[1:])
        element, value = field.split(":")
        match element:
            case "name":
                return (By.NAME, value)
            case "option":
                if "[" not in value:
                    t = (By.XPATH, f"//option[@{value}='()']".replace("()", "{}"))
                else:
                    value, *upper = value.split("[")
                    upper = "[".join(upper).rstrip("]")
                    upper = upper.replace("-", "=")
                    t = (
                        By.XPATH,
                        f"//select[@{upper}]//option[@{value}='()']".replace(
                            "()", "{}"
                        ),
                    )
                return t
            case "namedoption":
                t = (By.XPATH, f"//option[@{value}='()']".replace("()", "{}"))
                return t
            case _:
                raise NotImplementedError

    @staticmethod
    def spec_field_from_element(element: object) -> str:
        if isinstance(element, tuple) and len(element) == 2:
            target = str(element[1])
        else:
            target = str(element)
        match = re.search(r"product\[([^\]]+)\]", target)
        if match:
            return match.group(1)
        return target

    @classmethod
    def format_key(cls, field: str, key: str, category: str = None) -> str | None:
        debug_log = cls.debug_log_path()
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"    format_key called: field='{field}', key='{key}', category='{category}'\n")
        
        formattings = field.split(";")[1:]
        constants = None
        if field.startswith("in"):
            constants = field.split("[")[-1].split("]")[0].split(",")
            if key not in constants:
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    SKIPPED: key '{key}' not in constants {constants}\n")
                return None
                
        for formatting in formattings:
            with debug_log.open("a", encoding="utf-8") as f:
                f.write(f"    Applying formatting: '{formatting}'\n")
            
            if formatting == "capitalize":
                key = key.capitalize()
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    After capitalize: '{key}'\n")
            elif formatting == "dutch":
                original_key = key
                key = cls.get_mapped_key(key)
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    Dutch translation: '{original_key}' -> '{key}'\n")
            elif formatting.startswith("constant"):
                constant = formatting.split(":")[-1]
                if constant != key:
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(f"    SKIPPED: constant '{constant}' != key '{key}'\n")
                    return None
                    
        if category == "Shirts":
            if key == "Normal fit":
                key = "Regular fit"
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    Shirts category adjustment: Normal fit -> Regular fit\n")
            elif key == "Loose fit":
                key = "Comfort fit"
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    Shirts category adjustment: Loose fit -> Comfort fit\n")

        if "product[model]" in field:
            key = cls.FIT_OPTION_TITLES.get(key.lower(), key)
        
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"    format_key result: '{key}'\n")
        
        return key

    @classmethod
    def input_data(cls, driver: webdriver.Chrome, input: str, element: tuple[str, str]):
        by, path = element
        if by == By.XPATH:
            if input.isupper():
                input = input.title()
            select_name = cls.select_name_from_option_xpath(path)
            if select_name:
                cls.set_select_option_by_title(driver, select_name, input)
                return
            path = path.format(input)
            option = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((by, path))
            )
            cls.click_element_safely(driver, option)
        elif by == By.NAME:
            cls._set_text_input(driver, path, input)
        elif by == "CLICKABLE":
            select, option = path.split("=")
            by, field = select.split(":")
            if by == "name":
                cls.set_select_option_by_title(driver, field, input)
                return
            select_element = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((by, field))
            )
            cls.click_element_safely(driver, select_element)
            by, path = cls.get_element(option)
            select = "=".join((select.split(":")[0], f"'{select.split(':')[1]}'"))
            path = path.format(input)
            path = f"//select[@{select}]{path}"
            option = WebDriverWait(driver, 2).until(
                EC.presence_of_element_located((by, path))
            )
            cls.click_element_safely(driver, option)

    @staticmethod
    def select_name_from_option_xpath(path: str) -> str | None:
        match = re.search(r"//select\[@name=(['\"])(.*?)\1\]//option", path)
        return match.group(2) if match else None

    @classmethod
    def _set_text_input(
        cls, driver: webdriver.Chrome, field_name: str, value: str | float | int
    ) -> None:
        value_text = "" if value is None else str(value)
        last_error: Exception | None = None

        for _ in range(3):
            try:
                element_input = WebDriverWait(driver, 10).until(
                    EC.visibility_of_element_located((By.NAME, field_name))
                )
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                    element_input,
                )
                cls.random_wait(1)
                try:
                    element_input.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", element_input)
                element_input.clear()
                element_input.send_keys(value_text)
                current_value = (element_input.get_attribute("value") or "").strip()
                if current_value == value_text:
                    return
            except Exception as exc:
                last_error = exc

            try:
                element_input = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.NAME, field_name))
                )
                driver.execute_script(
                    """
                    const el = arguments[0];
                    const value = arguments[1];
                    el.removeAttribute('readonly');
                    el.removeAttribute('disabled');
                    el.focus();
                    el.value = '';
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    """,
                    element_input,
                    value_text,
                )
                current_value = (element_input.get_attribute("value") or "").strip()
                if current_value == value_text:
                    return
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Could not set Magento field: {field_name}")

    @classmethod
    def _image_dimensions(cls, image_path: Path) -> tuple[int, int] | None:
        try:
            with image_path.open("rb") as f:
                header = f.read(32)
        except OSError:
            return None

        # PNG: width/height are stored in IHDR at bytes 16..24.
        if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
            width = int.from_bytes(header[16:20], "big")
            height = int.from_bytes(header[20:24], "big")
            return width, height

        # JPEG: scan markers until Start Of Frame segment.
        if header[:2] == b"\xff\xd8":
            try:
                with image_path.open("rb") as f:
                    f.read(2)  # SOI
                    while True:
                        marker_start = f.read(1)
                        if not marker_start:
                            return None
                        if marker_start != b"\xff":
                            continue

                        marker = f.read(1)
                        while marker == b"\xff":
                            marker = f.read(1)
                        if not marker:
                            return None

                        marker_byte = marker[0]
                        if marker_byte in (0xD9, 0xDA):  # EOI/SOS without SOF found
                            return None

                        segment_length_bytes = f.read(2)
                        if len(segment_length_bytes) != 2:
                            return None
                        segment_length = int.from_bytes(segment_length_bytes, "big")
                        if segment_length < 2:
                            return None

                        if marker_byte in {
                            0xC0,
                            0xC1,
                            0xC2,
                            0xC3,
                            0xC5,
                            0xC6,
                            0xC7,
                            0xC9,
                            0xCA,
                            0xCB,
                            0xCD,
                            0xCE,
                            0xCF,
                        }:
                            sof = f.read(5)
                            if len(sof) != 5:
                                return None
                            height = int.from_bytes(sof[1:3], "big")
                            width = int.from_bytes(sof[3:5], "big")
                            return width, height

                        # Move to next marker (length includes its own 2 bytes).
                        f.seek(segment_length - 2, os.SEEK_CUR)
            except OSError:
                return None

        # GIF
        if header[:6] in (b"GIF87a", b"GIF89a") and len(header) >= 10:
            width = int.from_bytes(header[6:8], "little")
            height = int.from_bytes(header[8:10], "little")
            return width, height

        return None

    @classmethod
    def _is_placeholder_image(cls, image_path: Path) -> bool:
        dimensions = cls._image_dimensions(image_path)
        return dimensions == cls.PLACEHOLDER_IMAGE_DIMENSIONS

    @classmethod
    def _get_uploadable_images(cls, sku: str) -> list[Path]:
        images_path = cls.products_path() / sku
        if not images_path.exists() or not images_path.is_dir():
            return []

        images = sorted(
            images_path.glob("*"),
            key=lambda x: int(x.stem.split("_")[-1]) if x.stem.split("_")[-1].isdigit() else 0,
        )

        uploadable: list[Path] = []
        for image in images:
            if not image.is_file():
                continue
            try:
                file_size = image.stat().st_size
            except OSError:
                continue
            if file_size < 1000:
                continue
            if cls._is_placeholder_image(image):
                continue
            uploadable.append(image)
        return uploadable

    @classmethod
    def insert_images(cls, driver: webdriver.Chrome, sku: str):
        images_path = cls.products_path() / sku
        print(f"Looking for images in: {images_path.resolve()}")
        print(f"Images path exists: {images_path.exists()}")
        print(f"PRODUCTS_PATH: {cls.products_path()}")
        print(f"Current working directory: {os.getcwd()}")

        images_path.mkdir(exist_ok=True, parents=True)

        images = cls._get_uploadable_images(sku)
        print(f"Found {len(images)} uploadable images for SKU {sku}")

        if not images:
            print(f"No uploadable images found for SKU {sku} in {images_path}")
            return

        # Wait for the <span> element with the text 'Images And Videos' to be clickable and click on it
        images_and_videos_span = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//span[text()='Images And Videos']"))
        )
        images_and_videos_span.click()
        cls.random_wait(2)

        # Wait for the input element with the id 'fileupload' to be clickable and click on it
        for i, image in enumerate(images):
            try:
                print(f"Uploading image {i+1}/{len(images)}: {image.name}")
                
                # Verify the image file exists and is readable
                if not image.exists():
                    print(f"Image file does not exist: {image}")
                    continue
                    
                if not image.is_file():
                    print(f"Path is not a file: {image}")
                    continue
                    
                # Check file size
                file_size = image.stat().st_size
                if file_size < 1000:  # Less than 1KB is probably not a valid image
                    print(f"Image file too small ({file_size} bytes): {image.name}")
                    continue
                    
                cls.random_wait(2)
                file_input = driver.find_element(By.ID, "fileupload")
                cls.random_wait(2)
                
                # Use absolute path for file upload
                absolute_path = str(image.resolve())
                print(f"Sending file path: {absolute_path}")
                
                # Clear any existing value in the input
                file_input.clear()
                file_input.send_keys(absolute_path)
                cls.random_wait(3)  # Give more time for upload
                
                print(f"Successfully uploaded image {i+1}/{len(images)}")
                
            except Exception as e:
                print(f"Error uploading image {image.name}: {e}")
                import traceback
                traceback.print_exc()
                continue

    @classmethod
    def add_sizes(cls, driver: webdriver.Chrome, sizes: str):
        def safe_click(element, name: str) -> bool:
            try:
                element.click()
                return True
            except Exception:
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                        element,
                    )
                    driver.execute_script("arguments[0].click();", element)
                    return True
                except Exception as click_error:
                    print(f"Could not click {name}: {click_error}")
                    return False

        def find_first(
            candidates: tuple[tuple[str, str], ...],
            timeout: float = 10.0,
            clickable: bool = False,
        ):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for by, selector in candidates:
                    try:
                        elements = driver.find_elements(by, selector)
                    except Exception:
                        continue
                    for element in elements:
                        try:
                            if not element.is_displayed():
                                continue
                            if clickable and not element.is_enabled():
                                continue
                            return element
                        except Exception:
                            continue
                time.sleep(0.2)
            return None

        def find_all_visible(
            candidates: tuple[tuple[str, str], ...],
            timeout: float = 8.0,
        ) -> list:
            deadline = time.monotonic() + timeout
            last_seen: list = []
            while time.monotonic() < deadline:
                visible: list = []
                seen_ids: set[int] = set()
                for by, selector in candidates:
                    try:
                        elements = driver.find_elements(by, selector)
                    except Exception:
                        continue
                    for element in elements:
                        try:
                            if not element.is_displayed():
                                continue
                            element_id = id(element)
                            if element_id in seen_ids:
                                continue
                            seen_ids.add(element_id)
                            visible.append(element)
                        except Exception:
                            continue
                if visible:
                    return visible
                last_seen = visible
                time.sleep(0.2)
            return last_seen

        def parse_sizes(value: str | list[str]) -> list[str]:
            if isinstance(value, list):
                raw_sizes = value
            elif isinstance(value, str):
                try:
                    parsed = ast.literal_eval(value)
                    raw_sizes = parsed if isinstance(parsed, list) else [parsed]
                except Exception:
                    raw_sizes = [
                        item.strip().strip("'\"")
                        for item in value.strip().lstrip("[").rstrip("]").split(",")
                        if item.strip()
                    ]
            else:
                raw_sizes = [str(value)]

            cleaned: list[str] = []
            seen: set[str] = set()
            for raw in raw_sizes:
                size = str(raw).strip().strip("'\"")
                if not size:
                    continue
                normalized = size.upper() if re.search(r"[A-Za-z]", size) else size
                if normalized in seen:
                    continue
                seen.add(normalized)
                cleaned.append(normalized)
            return cleaned

        try:
            print(f"Starting size configuration for sizes: {sizes}")
            
            # Wait for the button with the data-index 'create_configurable_products_button' to be clickable and click on it
            print("Looking for create configurable products button...")
            create_configurable_products_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//button[@data-index='create_configurable_products_button']")
                )
            )
            if not safe_click(create_configurable_products_button, "create configurable products button"):
                raise Exception("Could not click create configurable products button")
            print("Clicked create configurable products button")

            cls.random_wait(2)

            # Magento can render this step with different checkbox markup depending on version/theme.
            print("Looking for size checkboxes...")
            checkboxes = find_all_visible(
                (
                    (By.CSS_SELECTOR, ".data-grid-checkbox-cell-inner"),
                    (By.CSS_SELECTOR, "td.data-grid-checkbox-cell input[type='checkbox']"),
                    (By.CSS_SELECTOR, "input.admin__control-checkbox"),
                ),
                timeout=12.0,
            )
            print(f"Found {len(checkboxes)} checkboxes")

            if checkboxes:
                if safe_click(checkboxes[-1], "last size checkbox"):
                    print("Clicked last checkbox")
            else:
                print("No size checkbox found on this step, continuing to next step")

            cls.random_wait(2)

            # Wait for the next button to be clickable and click on it
            print("Looking for next button...")
            next_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "action-next-step"))
            )
            if not safe_click(next_button, "first next button"):
                raise Exception("Could not click first next button")
            print("Clicked next button")

            cls.random_wait(2)
            sizes_list = parse_sizes(sizes)
            print(f"Processing sizes: {sizes_list}")

            # Click on the labels that match the sizes
            selected_sizes = 0
            for size in sizes_list:
                print(f"Looking for size label: {size}")
                size_label = find_first(
                    (
                        (By.XPATH, f"//label[normalize-space(text())='{size}']"),
                        (
                            By.XPATH,
                            f"//*[contains(@class,'admin__field-option')]//label[normalize-space(.)='{size}']",
                        ),
                    ),
                    timeout=3.0,
                    clickable=True,
                )
                if size_label is None:
                    print(f"Size not found in Magento options, skipping: {size}")
                    continue
                if safe_click(size_label, f"size label {size}"):
                    selected_sizes += 1
                    print(f"Clicked size: {size}")

            if selected_sizes == 0:
                raise Exception("No requested sizes could be selected in Magento")

            print("Clicking next button for size selection...")
            next_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "action-next-step"))
            )
            if not safe_click(next_button, "size selection next button"):
                raise Exception("Could not click size selection next button")
            cls.random_wait(2)

            print("Clicking next button for final step...")
            next_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "action-next-step"))
            )
            if not safe_click(next_button, "final step next button"):
                raise Exception("Could not click final step next button")
            cls.random_wait(2)

            print("Clicking next button to complete...")
            next_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "action-next-step"))
            )
            if not safe_click(next_button, "complete next button"):
                raise Exception("Could not click complete next button")
            cls.random_wait(2)
            
            print("Size configuration completed successfully")
            return True
        except Exception as e:
            print(f"Error in size configuration: {e}")
            import traceback
            traceback.print_exc()
            try:
                # <button class="action-close" data-role="closeBtn" type="button">
                #     <span>Close</span>
                # </button>
                print("Attempting to close configuration dialog...")
                close_btn = find_first(
                    (
                        (By.CSS_SELECTOR, "button[data-role='closeBtn']"),
                        (By.CLASS_NAME, "action-close"),
                    ),
                    timeout=2.0,
                    clickable=True,
                )
                if close_btn is not None:
                    safe_click(close_btn, "configuration dialog close button")
                    print("Configuration dialog closed")
                else:
                    driver.find_element(By.TAG_NAME, "body").send_keys("\u001b")
            except Exception as close_error:
                print(f"Could not close configuration dialog: {close_error}")
                pass
            time.sleep(2)
            return False

        return True

    @classmethod
    def parse_variant_prices(cls, product: pd.Series) -> dict[str, str]:
        raw_value = product.get("variant_prices", None)
        if raw_value is None or str(raw_value) == "nan" or str(raw_value).strip() == "":
            return {}
        if isinstance(raw_value, dict):
            parsed = raw_value
        else:
            text = str(raw_value)
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = ast.literal_eval(text)
        if not isinstance(parsed, dict):
            return {}

        prices: dict[str, str] = {}
        for size, price in parsed.items():
            price_text = str(price).replace("\u20ac", "").strip().replace(",", ".")
            try:
                price_text = f"{float(price_text):.2f}"
            except ValueError:
                pass
            prices[str(size).strip()] = price_text
        return prices

    @classmethod
    def update_variant_prices(cls, driver: webdriver.Chrome, product: pd.Series) -> None:
        variant_prices = cls.parse_variant_prices(product)
        if not variant_prices:
            return

        debug_log = cls.debug_log_path()
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"Updating variant prices for {product.get('sku')}: {variant_prices}\n")

        expected_sizes = set(variant_prices)
        try:
            row_matches = WebDriverWait(driver, 60).until(
                lambda current_driver: cls.ready_variant_row_matches(
                    current_driver,
                    expected_sizes,
                )
            )
        except TimeoutException as ex:
            all_rows = driver.find_elements(
                By.CSS_SELECTOR,
                "table.data-grid tbody tr, tr.data-row",
            )
            snapshots = [
                cls.row_text(driver, row).replace("\n", " ")[:300]
                for row in all_rows[:10]
            ]
            with debug_log.open("a", encoding="utf-8") as f:
                f.write(
                    "Could not find generated variant rows for expected sizes. "
                    f"Rows seen: {len(all_rows)}. Snapshots: {snapshots}\n"
                )
            raise Exception(
                "Could not find generated variant rows for sizes: "
                + ", ".join(sorted(expected_sizes))
            ) from ex

        matched_sizes: set[str] = set()
        for row, size in row_matches:
            inputs = []
            for input_element in row.find_elements(By.CSS_SELECTOR, "input"):
                try:
                    if input_element.is_displayed() and input_element.is_enabled():
                        inputs.append(input_element)
                except Exception:
                    continue

            price_input = cls._find_variant_price_input(inputs)
            if price_input is None:
                if cls.variant_price_can_inherit_parent_price(
                    product,
                    variant_prices[size],
                ):
                    matched_sizes.add(size)
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(
                            f"No price input for size {size}; "
                            "using inherited parent price.\n"
                        )
                    continue
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"Could not find price input for size {size}\n")
                continue

            price_value = variant_prices[size]
            try:
                price_input.clear()
                price_input.send_keys(price_value)
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                    price_input,
                )
                matched_sizes.add(size)
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"Set size {size} price to {price_value}\n")
            except Exception as ex:
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"Failed to set size {size} price to {price_value}: {ex}\n")

        missing_sizes = sorted(set(variant_prices) - matched_sizes)
        if missing_sizes:
            raise Exception(
                "Could not update variant prices for sizes: "
                + ", ".join(missing_sizes)
            )

    @classmethod
    def variant_price_can_inherit_parent_price(
        cls,
        product: pd.Series,
        variant_price: object,
    ) -> bool:
        try:
            parent_price = f"{float(str(product.get('rrp', '')).replace(',', '.')):.2f}"
            child_price = f"{float(str(variant_price).replace(',', '.')):.2f}"
        except ValueError:
            return False
        return parent_price == child_price

    @classmethod
    def ready_variant_row_matches(
        cls,
        driver: webdriver.Chrome,
        expected_sizes: set[str],
    ) -> list[tuple[object, str]] | bool:
        rows = driver.find_elements(
            By.CSS_SELECTOR,
            "table.data-grid tbody tr, tr.data-row",
        )
        matches = cls.matching_variant_rows(driver, rows, expected_sizes)
        matched_sizes = {size for _, size in matches}
        return matches if matched_sizes == expected_sizes else False

    @classmethod
    def matching_variant_rows(
        cls,
        driver: webdriver.Chrome | None,
        rows: list,
        expected_sizes: set[str],
    ) -> list[tuple[object, str]]:
        matches: list[tuple[object, str]] = []
        seen_sizes: set[str] = set()
        for row in rows:
            size = cls.variant_size_from_row_text(
                cls.row_text(driver, row),
                expected_sizes,
            )
            if not size or size in seen_sizes:
                continue
            matches.append((row, size))
            seen_sizes.add(size)
        return matches

    @classmethod
    def row_text(cls, driver: webdriver.Chrome | None, row) -> str:
        parts = []
        try:
            parts.append(row.text or "")
        except Exception:
            pass
        if driver is not None:
            try:
                parts.append(
                    driver.execute_script(
                        "return arguments[0].innerText || arguments[0].textContent || '';",
                        row,
                    )
                    or ""
                )
            except Exception:
                pass
        try:
            parts.append(row.get_attribute("textContent") or "")
        except Exception:
            pass
        return "\n".join(part for part in parts if part)

    @staticmethod
    def variant_size_from_row_text(row_text: str, expected_sizes: set[str]) -> str | None:
        text = re.sub(r"\s+", " ", str(row_text or "")).strip()
        if not text:
            return None

        size_match = re.search(r"\bMaat\s*:?\s*([A-Za-z0-9./-]+)", text, re.IGNORECASE)
        if size_match and size_match.group(1).strip() in expected_sizes:
            return size_match.group(1).strip()

        for candidate in sorted(expected_sizes, key=len, reverse=True):
            if re.search(rf"(?<!\d){re.escape(candidate)}(?!\d)", text):
                return candidate
        return None

    @staticmethod
    def _find_variant_price_input(inputs: list) -> object | None:
        for input_element in inputs:
            attributes = []
            for attr in ("name", "data-bind", "data-role", "aria-label", "id", "class"):
                try:
                    attributes.append(input_element.get_attribute(attr) or "")
                except Exception:
                    pass
            joined = " ".join(attributes).lower()
            if "price" in joined or "prijs" in joined or "preis" in joined:
                return input_element
        if len(inputs) >= 3:
            return inputs[2]
        return inputs[0] if inputs else None

    @classmethod
    def input_default(cls, driver: webdriver.Chrome, product: pd.Series):
        profile = cls.profile()
        weight_input = driver.find_element(By.NAME, "product[weight]")
        weight_input.clear()
        weight_input.send_keys(profile.default_weight)
        cls.select_option_by_title(
            driver,
            "product[manufacturer]",
            profile.manufacturer_title,
        )
        cls.select_option_by_title(
            driver,
            "product[borstzak]",
            profile.default_chest_pocket,
        )
        categories_select = driver.find_element(
            By.XPATH,
            "//div[@class='admin__action-multiselect-text' and text()='Select...']",
        )
        cls.click_element_safely(driver, categories_select)
        path = "//label[@class='admin__action-multiselect-label']//span[@data-bind='text: option.label' and text()='Assortiment']"
        assortimant_label = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, path))
        )
        cls.click_element_safely(driver, assortimant_label)
        path = (
            '//label[@class="admin__action-multiselect-label" and text()="{}"]'.format(
                cls.get_mapped_key(product["category"])
            )
        )
        category_label = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, path))
        )
        cls.click_element_safely(driver, category_label)
        close_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[@data-action='close-advanced-select']",
                )
            )
        )
        cls.click_element_safely(driver, close_button)

    @classmethod
    def normalize_attribute_value(cls, field_key: str, value: object) -> str:
        text = "" if value is None else str(value).strip()
        if field_key == "quality":
            text = re.split(r"\s+NOS\s*:", text, maxsplit=1, flags=re.IGNORECASE)[0]
        elif field_key == "Productnaam":
            text = re.sub(r"\s+nos\s*:\s*ja\b", "", text, flags=re.IGNORECASE)
        return text.strip()

    @classmethod
    def option_title_xpath(cls, select_name: str, title: str) -> str:
        target = str(title).strip().lower()
        name_literal = cls.xpath_literal(select_name)
        title_literal = cls.xpath_literal(target)
        data_title = (
            "translate(normalize-space(@data-title), "
            f"{cls.xpath_literal(cls.XPATH_UPPER)}, {cls.xpath_literal(cls.XPATH_LOWER)})"
        )
        option_text = (
            "translate(normalize-space(.), "
            f"{cls.xpath_literal(cls.XPATH_UPPER)}, {cls.xpath_literal(cls.XPATH_LOWER)})"
        )
        return (
            f"//select[@name={name_literal}]//option["
            f"{data_title}={title_literal} or {option_text}={title_literal}]"
        )

    @staticmethod
    def xpath_literal(value: str) -> str:
        text = str(value)
        if '"' not in text:
            return f'"{text}"'
        if "'" not in text:
            return f"'{text}'"
        parts = text.split('"')
        return "concat(" + ', \'"\', '.join(f'"{part}"' for part in parts) + ")"

    @classmethod
    def select_option_by_title(
        cls,
        driver: webdriver.Chrome,
        select_name: str,
        title: str,
        timeout: int = 10,
    ) -> None:
        cls.set_select_option_by_title(driver, select_name, title, timeout=timeout)

    @classmethod
    def set_select_option_by_title(
        cls,
        driver: webdriver.Chrome,
        select_name: str,
        title: str,
        timeout: int = 10,
    ) -> None:
        select_element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.NAME, select_name))
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            select_element,
        )
        target = str(title).strip().lower()
        WebDriverWait(driver, timeout).until(
            lambda current_driver: current_driver.execute_script(
                """
                const select = arguments[0];
                const target = arguments[1];
                const normalize = (value) => (value || '').trim().toLowerCase();
                const options = Array.from(select.options || select.querySelectorAll('option'));
                const option = options.find((item) =>
                    normalize(item.getAttribute('data-title')) === target ||
                    normalize(item.textContent) === target
                );
                if (!option) {
                    return false;
                }
                option.selected = true;
                select.value = option.value;
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
                select.dispatchEvent(new Event('blur', { bubbles: true }));
                return true;
                """,
                select_element,
                target,
            )
        )

    @staticmethod
    def click_element_safely(driver: webdriver.Chrome, element) -> None:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            element,
        )
        try:
            element.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", element)

    @classmethod
    def get_mapped_key(cls, key: str) -> str:
        debug_log = cls.debug_log_path()
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"      get_mapped_key called: '{key}'\n")
            f.write(f"      Looking for key.lower(): '{key.lower()}'\n")
            f.write(f"      Available keys: {list(cls.TRANSLATE_MAPPING.keys())[:10]}...\n")
        
        value = cls.TRANSLATE_MAPPING.get(key.lower(), "").strip()
        
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"      Translation result: '{value}'\n")
        
        if not value:
            print(f"Key not found: {key}")
            with debug_log.open("a", encoding="utf-8") as f:
                f.write(f"      ERROR: Key '{key}' not found in translation mapping!\n")
            # write to file
            with cls.translation_errors_path().open("a", encoding="utf-8") as log_file:
                log_file.write(f"Missing {key}\n")
        else:
            with debug_log.open("a", encoding="utf-8") as f:
                f.write(f"      SUCCESS: '{key}' -> '{value}'\n")
        
        return value

    @staticmethod
    def change_maattabel(
        driver: webdriver.Chrome, value: str = "Profuomo slim fit truien"
    ):
        sizetable_select = driver.find_element(By.NAME, "product[sizetable]")
        sizetable_select.click()

        maattabel_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, f"//option[@data-title='{value}']"))
        )
        maattabel_option.click()

    @classmethod
    def blank_color(cls, driver: webdriver.Chrome):
        color_select = driver.find_element(By.NAME, "product[color]")
        color_select.click()

        blank_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//select[@name='product[color]']//option[@value='']",
                )
            )
        )
        blank_option.click()

    @classmethod
    def fill_form(cls, driver: webdriver.Chrome, product: pd.Series):
        sku = product.get("sku", "UNKNOWN")
        debug_log = cls.debug_log_path()
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"\n--- FILL_FORM for SKU: {sku} ---\n")
        
        data = cls.fetch_data(product)
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(f"Data to fill: {data}\n")
            f.write(f"Number of fields to fill: {len(data)}\n")
        
        counter = 0
        # wait until the element with the id 'product[name]' is present
        WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.NAME, "product[name]"))
        )
        productnaam_nl = product["name"].replace("SC SF ", "").title()
        for key, value in data.items():
            with debug_log.open("a", encoding="utf-8") as f:
                f.write(f"  Filling field: {key} -> {value}\n")
            try:
                if value == "cuff" and product["cuff"] == "NO CUFF":
                    continue
                cls.input_data(driver, key, value)
                driver.execute_script("window.scrollBy(0, 200);")
                if counter == 0:
                    productnaam_nl = key
                    # click on <span data-bind="i18n: label">Search Engine Optimization</span>
                    seo_span = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable(
                            (
                                By.XPATH,
                                "//span[@data-bind='i18n: label' and text()='Search Engine Optimization']",
                            )
                        )
                    )
                    seo_span.click()
                    meta_description = cls.gen_metadescription(product, productnaam_nl)
                    url_key_input = driver.find_element(By.NAME, "product[url_key]")
                    url_key_input.clear()
                    url_key_input.send_keys(
                        key.replace(" ", "-") + "-" + product["sku"]
                    )
                    meta_description_input = driver.find_element(
                        By.NAME, "product[meta_description]"
                    )
                    meta_description_input.clear()
                    meta_description_input.send_keys(meta_description)
            except Exception as e:
                with debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"    ERROR filling field {key}: {e}\n")
                    f.write(f"    Field value: {value}\n")
                    f.write(f"    Exception type: {type(e).__name__}\n")
                    import traceback
                    f.write(f"    Traceback: {traceback.format_exc()}\n")
                
                print(f"Error filling field {key} for product {product['sku']}: {e}")
                print(f"Field value: {value}")
                # also write to file
                with cls.translation_errors_path().open(
                    "a", encoding="utf-8"
                ) as log_file:
                    log_file.write(f"{product['sku']}, {key}, {value}\n")
                cls.log_missing_magento_spec(
                    sku=product["sku"],
                    field=cls.spec_field_from_element(value),
                    source_value=key,
                    mapped_value=key,
                    target=value,
                    reason=f"{type(e).__name__}: {e}",
                )
                
                # For critical fields, raise the exception to stop processing
                critical_input_targets = {"product[name]", "product[sku]", "product[price]"}
                is_critical = (
                    isinstance(value, tuple)
                    and len(value) == 2
                    and value[0] == By.NAME
                    and value[1] in critical_input_targets
                )
                if is_critical:
                    with debug_log.open("a", encoding="utf-8") as f:
                        f.write(
                            f"    CRITICAL FIELD FAILURE: target={value} input='{key}' - stopping product upload\n"
                        )
                    print(
                        f"Critical field failed for product {product['sku']} "
                        f"(target={value}, input='{key}'), stopping product upload"
                    )
                    raise e
            counter += 1
            if counter == 4:
                cls.input_default(driver, product)
        mapped_category = cls.get_mapped_key(product["category"])
        size_table = cls.profile().size_table_for(
            source_category=product["category"],
            fit=product.get("fit", ""),
            mapped_category=mapped_category,
            sleeve=product.get("sleeve", ""),
        )
        if size_table:
            cls.change_maattabel(driver, size_table)

        if TEST:
            cls.blank_color(driver)
        cls.add_description(driver, product, productnaam_nl)
        driver.execute_script("window.scrollBy(0, 800);")
        cls.insert_images(driver, product["sku"])
        added_sizes = False
        tries = 0
        while not added_sizes and tries < 3:
            added_sizes = cls.add_sizes(driver, product["sizes"])
            tries += 1
            if not added_sizes and tries < 3:
                time.sleep(0.5)
        if not added_sizes:
            raise Exception("Sizes not added")
        cls.update_variant_prices(driver, product)

    @staticmethod
    def save_product(driver: webdriver.Chrome):
        # Wait for the button with data-ui-id="save-button-dropdown" to be clickable and click on it
        save_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[@data-ui-id='save-button-dropdown']",
                )
            )
        )
        save_button.click()

        save_and_new_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "save_and_new"))
        )
        save_and_new_button.click()

    @classmethod
    def register_product(cls, driver: webdriver.Chrome, product: pd.Series):
        cls.fill_form(driver, product)

        cls.save_product(driver)

        time.sleep(10)

        while True:
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "product[name]"))
                )
                break
            except Exception:
                pass
        try:
            driver.find_element(
                By.CSS_SELECTOR, "[data-ui-id='messages-message-error']"
            )
            print("URL Error")
            # write the sku to the file urlerror.log
            with Path("urlerror.log").open("a", encoding="utf-8") as log_file:
                log_file.write(f"{product['sku']}\n")
            driver.refresh()
            raise RuntimeError
        except NoSuchElementException:
            pass

    @classmethod
    def get_products(cls, csv_path: str | Path | None = None) -> pd.DataFrame:
        if csv_path is None:
            csv_path = cls.products_path() / cls.profile().product_aggregate_filename
        products = pd.read_csv(csv_path, sep=",", quotechar='"')
        try:
            with cls.profile().done_path.open(encoding="utf-8-sig") as f:
                done = f.read().splitlines()
        except FileNotFoundError:
            done = []
        products = products[~products["sku"].isin(done)]
        done_products = products[products["sku"].isin(done)]
        cls.update_input_csv(done_products)
        return products

    @classmethod
    def update_input_csv(cls, done_products: pd.DataFrame):
        def get_sizes(product: pd.Series):
            return (
                product["sizes"]
                .replace("[", "")
                .replace("]", "")
                .replace(" ", "")
                .replace("'", "")
            )

        done_products = done_products[["sku", "sizes"]]
        lines = {
            f"{product['sku']},{get_sizes(product)}"
            for _, product in done_products.iterrows()
        }
        input_csv_path = cls.input_csv_path()
        if input_csv_path.exists():
            with input_csv_path.open(encoding="utf-8-sig") as existing_products:
                lines.update(line.strip() for line in existing_products)
            with input_csv_path.open("w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    @classmethod
    def check_existing(
        cls, driver: webdriver.Chrome, products: pd.DataFrame, done_path: Path
    ) -> pd.DataFrame:
        # check if the mangento_products.csv file exists, if so, use that to determine.
        # the header of the file is ID,Thumbnail,Name,Type,"Attribute Set",SKU,Price,"Quantity per Source","Salable Quantity",Visibility,Status,Websites,Maat,"Kraag type",Mouwlengte,"Default Original Qty",Materiaal,Borstzak,EAN,Capsule,Samenstelling,Duurzaamheid,"Creation Date"
        magento_products_path = Path("magento_products.csv")
        if magento_products_path.exists():
            print(f"Found existing magento_products.csv with {magento_products_path.stat().st_size} bytes")
            try:
                existing_products = pd.read_csv(magento_products_path)
                existing_skus = set(existing_products["SKU"])
                print(f"Found {len(existing_skus)} existing SKUs in magento_products.csv")
                products = products[~products["sku"].isin(existing_skus)]
                print(f"Filtered products to {len(products)} new products")
                
                # Read and deduplicate existing lines once
                lines = set()
                input_csv = cls.input_csv_path()
                if input_csv.exists():
                    with input_csv.open(encoding="utf-8-sig") as existing_products:
                        lines.update(line.strip() for line in existing_products)

                # Collect new lines for all products
                new_lines = []
                for _, product in products.iterrows():
                    sizes = (
                        product["sizes"]
                        .replace("[", "")
                        .replace("]", "")
                        .replace(" ", "")
                        .replace("'", "")
                    )
                    new_line = product["sku"] + "," + sizes
                    new_lines.append(new_line)

                # Combine and write once
                lines.update(new_lines)
                with input_csv.open("w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                return products
            except Exception as e:
                print(f"Error reading magento_products.csv: {e}")
                print("Continuing without existing product check...")
                return products
        existing = []
        for _, product in products.iterrows():
            try:
                cls.random_wait()
                while True:
                    # first check if the button with data-action="grid-filter-reset" exists:
                    try:
                        driver.execute_script("window.scrollTo(0, 0);")
                        reset_buttons = driver.find_elements(
                            By.CSS_SELECTOR, "[data-action='grid-filter-reset']"
                        )
                        if reset_buttons:
                            # scroll to the top of the page
                            # wait until the button wiht data-action="grid-filter-reset" us clickable and click on it
                            time.sleep(0.2)
                            reset_button = WebDriverWait(driver, 60).until(
                                EC.element_to_be_clickable(
                                    (
                                        By.XPATH,
                                        "(//button[@data-action='grid-filter-reset'])[1]",
                                    )
                                )
                            )
                            reset_button.click()
                    except Exception:
                        pass
                    time.sleep(2)
                    input_field = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.ID, "fulltext"))
                    )
                    input_field.clear()
                    input_field.send_keys(product["sku"])
                    search_button = WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable(
                            (
                                By.XPATH,
                                "//button[@aria-label='Search']",
                            )
                        )
                    )
                    search_button.click()
                    time.sleep(2)
                    try:
                        input_field = WebDriverWait(driver, 60).until(
                            EC.element_to_be_clickable((By.ID, "fulltext"))
                        )
                        # find element: <div class="data-grid-cell-content white-space-preserved" data-bind="html: $col.getLabelUnsanitizedHtml($row())">PPVH30043D</div>
                        tds = driver.find_elements(
                            By.XPATH,
                            "//div[@class='data-grid-cell-content white-space-preserved']",
                        )
                        for sku in tds:
                            if sku.text == product["sku"]:
                                raise ProductFoundException("Product already exists")
                        #  find element: "//tr[@class='data-grid-tr-no-data']"
                        driver.find_element(
                            By.XPATH, "//tr[@class='data-grid-tr-no-data']"
                        )
                        break
                    except ProductFoundException:
                        existing.append(product)
                        with done_path.open("a", encoding="utf-8") as f:
                            f.write(product["sku"] + "\n")
                        sizes = (
                            product["sizes"]
                            .replace("[", "")
                            .replace("]", "")
                            .replace(" ", "")
                            .replace("'", "")
                        )
                        lines = set()
                        new_line = product["sku"] + "," + sizes
                        # Read existing lines and add them to the set (deduplicates)
                        input_csv = cls.input_csv_path()
                        if input_csv.exists():
                            with input_csv.open(
                                encoding="utf-8-sig"
                            ) as existing_products:
                                lines.update(line.strip() for line in existing_products)

                        # Add the new product line to the set
                        lines.add(new_line)

                        # Write the unique lines back to the file
                        with input_csv.open("w", encoding="utf-8") as f:
                            f.write("\n".join(lines) + "\n")
                        break
                    except ElementClickInterceptedException:
                        pass
                    except NoSuchElementException:
                        pass
                    except Exception:
                        pass
            except Exception:
                pass
        products = products[~products["sku"].isin(existing)]
        return products

    @classmethod
    def has_valid_images(cls, sku: str) -> bool:
        return len(cls._get_uploadable_images(sku)) > 0

    @staticmethod
    def is_magento_ready(product: pd.Series) -> bool:
        raw_value = product.get("magento_ready", True)
        if raw_value is None or str(raw_value) == "nan" or str(raw_value).strip() == "":
            return True
        return str(raw_value).strip().lower() not in {"false", "0", "no", "nee"}

    @classmethod
    def register_products(
        cls, csv_path: str | None = None, test: bool = False, supplier: str = "profuomo"
    ) -> dict[str, str]:
        profile = cls.configure_supplier(supplier)
        cls._get_mapping()
        data = {"message": "", "error": ""}
        cls.reset_missing_specs_report()

        products = cls.get_products(csv_path)
        
        # Create debug log file
        debug_log_path = cls.debug_log_path()
        with debug_log_path.open("w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("PRODUCT UPLOAD DEBUG LOG\n")
            f.write("=" * 80 + "\n")
            f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"CSV Path: {csv_path}\n")
            f.write(f"Supplier: {profile.key}\n")
            f.write(f"Test Mode: {test}\n")
            f.write(f"Total Products: {len(products)}\n")
            f.write(f"Translation Mappings Loaded: {len(cls.TRANSLATE_MAPPING)}\n")
            f.write("=" * 80 + "\n\n")

        # set up webdriver
        options = webdriver.ChromeOptions()

        # Always use current directory for downloads
        prefs = {
            "download.default_directory": str(Path().resolve()),
            "download.prompt_for_download": False,
            "directory_upgrade": True,
            "safebrowsing.enabled": False,
        }
        options.add_experimental_option("prefs", prefs)

        driver = webdriver.Chrome(options=options)
        try:
            # set selenium wait time to 15 minutes
            driver.implicitly_wait(10)
            driver.maximize_window()
            # driver.implicitly_wait(1800)

            cls.magento_login(driver, test=test)
            done_path = profile.done_path
            failed_path = profile.failed_path

            cls.go_to_product_catalogue(driver)

            cls.download_current_products(driver)

            products = cls.check_existing(driver, products, done_path)

            cls.go_to_form(driver)

            processed_count = 0
            success_count = 0
            failed_count = 0
            skipped_no_images_count = 0
            skipped_not_ready_count = 0

            for _, product in filter(
                lambda product: product[1].get("Productnaam")
                and str(product[1].get("Productnaam")) != "nan",
                products.iterrows(),
                ):
                processed_count += 1
                sku = product["sku"]
                with debug_log_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"PROCESSING PRODUCT: {sku}\n")
                    f.write(f"{'='*60}\n")
                    f.write(f"Product data: {dict(product)}\n")
                    f.write(f"Productnaam: {product.get('Productnaam', 'MISSING')}\n")
                    f.write(f"Category: {product.get('category', 'MISSING')}\n")
                if not cls.is_magento_ready(product):
                    with debug_log_path.open("a", encoding="utf-8") as f:
                        f.write(
                            "SKIPPED: "
                            f"{sku} is not marked magento_ready. "
                            f"Reason: {product.get('blocked_reason', '')}\n"
                        )
                    print(f"Skipping {sku}: not marked magento_ready")
                    skipped_not_ready_count += 1
                    continue
                if not cls.has_valid_images(sku):
                    with debug_log_path.open("a", encoding="utf-8") as f:
                        f.write(f"SKIPPED: {sku} has no valid images in {cls.products_path() / sku}\n")
                    print(f"Skipping {sku}: no valid images found")
                    skipped_no_images_count += 1
                    continue
                
                try:
                    with debug_log_path.open("a", encoding="utf-8") as f:
                        f.write(f"Starting register_product for {sku}\n")
                    cls.register_product(driver, product)
                    with debug_log_path.open("a", encoding="utf-8") as f:
                        f.write(f"SUCCESS: Product {sku} registered successfully\n")
                    success_count += 1
                    with done_path.open("a", encoding="utf-8") as f:
                        f.write(product["sku"] + "\n")
                    sizes = (
                        product["sizes"]
                        .replace("[", "")
                        .replace("]", "")
                        .replace(" ", "")
                        .replace("'", "")
                    )
                    lines = set()
                    new_line = product["sku"] + "," + sizes
                    # Read existing lines and add them to the set (deduplicates)
                    input_csv = cls.input_csv_path()
                    if input_csv.exists():
                        with input_csv.open(encoding="utf-8-sig") as existing_products:
                            lines.update(line.strip() for line in existing_products)

                    # Add the new product line to the set
                    lines.add(new_line)

                    # Write the unique lines back to the file
                    with input_csv.open("w", encoding="utf-8") as f:
                        f.write("\n".join(lines) + "\n")
                except Exception as e:
                    with debug_log_path.open("a", encoding="utf-8") as f:
                        f.write(f"ERROR: Product {sku} failed with exception: {e}\n")
                        f.write(f"Exception type: {type(e).__name__}\n")
                        import traceback
                        f.write(f"Traceback:\n{traceback.format_exc()}\n")
                    driver.refresh()
                    cls.random_wait()
                    driver.execute_script("window.scrollTo(0, 0);")
                    with failed_path.open("a", encoding="utf-8") as f:
                        f.write(product["sku"] + "\n")
                    failed_count += 1
                    continue

            data["message"] = (
                "File uploaded successfully "
                f"({success_count} succeeded, {failed_count} failed, "
                f"{skipped_no_images_count} skipped no images, "
                f"{skipped_not_ready_count} skipped not ready, "
                f"{processed_count} processed)"
            )

        except Exception as ex:
            data["error"] = str(ex)
            data["message"] = ""

        finally:
            driver.quit()
            return data
