from pathlib import Path
import csv
import ast
import tempfile
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
    PROFUOMO_BATCH_SIZE = int(os.getenv("PROFUOMO_BATCH_SIZE", "800"))
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

    @classmethod
    def split_profuomo_batches(cls, source_path: Path) -> tuple[list[Path], Path | None]:
        batch_size = cls.PROFUOMO_BATCH_SIZE
        if batch_size <= 0:
            return [source_path], None

        with open(source_path, "r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.reader(csv_file)
            header = next(reader, None)
            if header is None:
                raise Exception(f"CSV has no header: {source_path.name}")
            rows = list(reader)

        if len(rows) <= batch_size:
            return [source_path], None

        temp_dir = Path(tempfile.mkdtemp(prefix="profuomo_batches_"))
        batch_files: list[Path] = []
        for batch_index, start in enumerate(range(0, len(rows), batch_size), start=1):
            end = start + batch_size
            batch_rows = rows[start:end]
            batch_path = temp_dir / f"{source_path.stem}_part_{batch_index:03d}.csv"
            with open(batch_path, "w", encoding="utf-8", newline="") as batch_file:
                writer = csv.writer(batch_file)
                writer.writerow(header)
                writer.writerows(batch_rows)
            batch_files.append(batch_path)

        return batch_files, temp_dir

    @staticmethod
    def cleanup_batch_dir(batch_dir: Path | None):
        if batch_dir is None or not batch_dir.exists():
            return
        for file_path in batch_dir.iterdir():
            try:
                file_path.unlink()
            except Exception:
                pass
        try:
            batch_dir.rmdir()
        except Exception:
            pass

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
        uploaded_parts = 0
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
                source_path = Path(filename).resolve()
                if not source_path.exists():
                    raise FileNotFoundError(f"File not found: {source_path}")

                upload_files = [source_path]
                batch_dir: Path | None = None
                if source_path.name == "profuomo_products.csv":
                    upload_files, batch_dir = cls.split_profuomo_batches(source_path)

                try:
                    for upload_file in upload_files:
                        cls.upload_single_file(driver, str(upload_file))
                        uploaded_parts += 1
                finally:
                    cls.cleanup_batch_dir(batch_dir)

            driver.get(cls.CRONFLAG_URL)
            time.sleep(3)
            status["message"] = (
                f"File uploaded successfully ({uploaded_parts} import part(s))"
            )

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
            # Use the same path resolution as config.py
            if getattr(sys, "frozen", False):
                # Running as PyInstaller executable
                BASE_DIR = Path(sys.executable).parent
            else:
                # Running as Python script
                BASE_DIR = Path(__file__).parent.parent
            
            mapping_file = BASE_DIR / "translate_mapping.txt"
            print(f"Loading translation mapping from: {mapping_file.resolve()}")
            print(f"File exists: {mapping_file.exists()}")
            print(f"Current working directory: {os.getcwd()}")
            print(f"Executable location: {sys.executable}")
            
            with mapping_file.open(encoding="utf-8-sig") as f:
                mapping = {}
                file = f.read()
                try:
                    for line in file.split("\n"):
                        if line != "":
                            key, value = line.strip().split(":")
                            mapping[key.strip().lower()] = value.strip()
                except ValueError:
                    pass
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
        
        # Log to debug file
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
            f.write(f"\n--- FETCH_DATA for SKU: {sku} ---\n")
            f.write(f"Product data: {dict(product)}\n")
            f.write(f"Category: {product.get('category', 'UNKNOWN')}\n")
            f.write(f"Available translation mappings: {len(cls.TRANSLATE_MAPPING)}\n")
        
        for key, value in cls.FORM_MAPPING.items():
            if (
                key not in product.index
                or str(product[key]) == "nan"
                or not product[key]
            ):
                # Special handling for overshirts: if collar is empty/nan, use "overshirt"
                if key == "collar" and product["category"] == "Overshirts":
                    product[key] = "overshirt"
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"  Special handling for overshirts collar: set to 'overshirt'\n")
                else:
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"  Skipping {key}: not in product or is nan/empty\n")
                    continue
                    
            if "|" in value:
                fields = value.split("|")
            else:
                fields = [value]
                
            for field in fields:
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"  Processing field: {field} for key: {key}\n")
                    f.write(f"    Original value: {product[key]}\n")
                
                if field == "Productnaam":
                    product[key] = "Profuomo " + product[key].replace("SC SF ", "")
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"    Productnaam processed: {product[key]}\n")
                    # Product name should not be translated, use as-is
                    element = cls.get_element(field)
                    data[product[key]] = element
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"    Added to data: {product[key]} -> {element}\n")
                    continue
                    
                k = cls.format_key(field, product[key], product["category"])
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    format_key result: {k}\n")
                
                if k is None:
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"    SKIPPED: format_key returned None\n")
                    continue
                    
                element = cls.get_element(field)
                data[k] = element
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    Added to data: {k} -> {element}\n")
        
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
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

    @classmethod
    def format_key(cls, field: str, key: str, category: str = None) -> str | None:
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
            f.write(f"    format_key called: field='{field}', key='{key}', category='{category}'\n")
        
        formattings = field.split(";")[1:]
        constants = None
        if field.startswith("in"):
            constants = field.split("[")[-1].split("]")[0].split(",")
            if key not in constants:
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    SKIPPED: key '{key}' not in constants {constants}\n")
                return None
                
        for formatting in formattings:
            with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                f.write(f"    Applying formatting: '{formatting}'\n")
            
            if formatting == "capitalize":
                key = key.capitalize()
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    After capitalize: '{key}'\n")
            elif formatting == "dutch":
                original_key = key
                key = cls.get_mapped_key(key)
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    Dutch translation: '{original_key}' -> '{key}'\n")
            elif formatting.startswith("constant"):
                constant = formatting.split(":")[-1]
                if constant != key:
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"    SKIPPED: constant '{constant}' != key '{key}'\n")
                    return None
                    
        if category == "Shirts":
            if key == "Normal fit":
                key = "Regular fit"
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    Shirts category adjustment: Normal fit -> Regular fit\n")
            elif key == "Loose fit":
                key = "Comfort fit"
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    Shirts category adjustment: Loose fit -> Comfort fit\n")
        
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
            f.write(f"    format_key result: '{key}'\n")
        
        return key

    @classmethod
    def input_data(cls, driver: webdriver.Chrome, input: str, element: tuple[str, str]):
        by, path = element
        if by == By.XPATH:
            if input.isupper():
                input = input.title()
            path = path.format(input)
            option = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((by, path))
            )
            option.click()
        elif by == By.NAME:
            element_input = driver.find_element(by, path)
            element_input.clear()
            element_input.send_keys(input)
        elif by == "CLICKABLE":
            select, option = path.split("=")
            by, field = select.split(":")
            select_element = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((by, field))
            )
            select_element.click()
            by, path = cls.get_element(option)
            select = "=".join((select.split(":")[0], f"'{select.split(':')[1]}'"))
            path = path.format(input)
            path = f"//select[@{select}]{path}"
            option = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((by, path))
            )
            option.click()

    @classmethod
    def insert_images(cls, driver: webdriver.Chrome, sku: str):
        images_path = Path(PRODUCTS_PATH, sku)
        print(f"Looking for images in: {images_path.resolve()}")
        print(f"Images path exists: {images_path.exists()}")
        print(f"PRODUCTS_PATH: {PRODUCTS_PATH}")
        print(f"Current working directory: {os.getcwd()}")
        
        images_path.mkdir(exist_ok=True, parents=True)
        
        # Get all image files
        images = sorted(
            images_path.glob("*"),
            key=lambda x: int(x.stem.split("_")[-1]) if x.stem.split("_")[-1].isdigit() else 0,
        )
        
        print(f"Found {len(images)} images for SKU {sku}")
        
        if not images:
            print(f"No images found for SKU {sku} in {images_path}")
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
    def input_default(cls, driver: webdriver.Chrome, product: pd.Series):
        weight_input = driver.find_element(By.NAME, "product[weight]")
        weight_input.clear()
        weight_input.send_keys("0.5")
        merk_select = driver.find_element(By.NAME, "product[manufacturer]")
        merk_select.click()
        profuomo_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//option[@data-title='Profuomo']"))
        )
        profuomo_option.click()
        borstzak_select = driver.find_element(By.NAME, "product[borstzak]")
        borstzak_select.click()
        zonder_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//option[@data-title='Zonder borstzak']",
                )
            )
        )
        zonder_option.click()
        categories_select = driver.find_element(
            By.XPATH,
            "//div[@class='admin__action-multiselect-text' and text()='Select...']",
        )
        categories_select.click()
        path = "//label[@class='admin__action-multiselect-label']//span[@data-bind='text: option.label' and text()='Assortiment']"
        assortimant_label = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, path))
        )
        assortimant_label.click()
        path = (
            '//label[@class="admin__action-multiselect-label" and text()="{}"]'.format(
                cls.get_mapped_key(product["category"])
            )
        )
        category_label = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, path))
        )
        category_label.click()
        close_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[@data-action='close-advanced-select']",
                )
            )
        )
        close_button.click()

    @classmethod
    def get_mapped_key(cls, key: str) -> str:
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
            f.write(f"      get_mapped_key called: '{key}'\n")
            f.write(f"      Looking for key.lower(): '{key.lower()}'\n")
            f.write(f"      Available keys: {list(cls.TRANSLATE_MAPPING.keys())[:10]}...\n")
        
        value = cls.TRANSLATE_MAPPING.get(key.lower(), "").strip()
        
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
            f.write(f"      Translation result: '{value}'\n")
        
        if not value:
            print(f"Key not found: {key}")
            with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                f.write(f"      ERROR: Key '{key}' not found in translation mapping!\n")
            # write to file
            with Path("translation_errors.log").open("a", encoding="utf-8") as log_file:
                log_file.write(f"Missing {key}\n")
        else:
            with Path("error_debug.txt").open("a", encoding="utf-8") as f:
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
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
            f.write(f"\n--- FILL_FORM for SKU: {sku} ---\n")
        
        data = cls.fetch_data(product)
        with Path("error_debug.txt").open("a", encoding="utf-8") as f:
            f.write(f"Data to fill: {data}\n")
            f.write(f"Number of fields to fill: {len(data)}\n")
        
        counter = 0
        # wait until the element with the id 'product[name]' is present
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.NAME, "product[name]"))
        )
        productnaam_nl = product["name"].replace("SC SF ", "").title()
        for key, value in data.items():
            with Path("error_debug.txt").open("a", encoding="utf-8") as f:
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
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"    ERROR filling field {key}: {e}\n")
                    f.write(f"    Field value: {value}\n")
                    f.write(f"    Exception type: {type(e).__name__}\n")
                    import traceback
                    f.write(f"    Traceback: {traceback.format_exc()}\n")
                
                print(f"Error filling field {key} for product {product['sku']}: {e}")
                print(f"Field value: {value}")
                # also write to file
                with Path("translation_errors.log").open(
                    "a", encoding="utf-8"
                ) as log_file:
                    log_file.write(f"{product['sku']}, {key}, {value}\n")
                
                # For critical fields, raise the exception to stop processing
                critical_fields = ["Productnaam", "sku", "rrp"]
                if key in critical_fields:
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"    CRITICAL FIELD FAILURE: {key} - stopping product upload\n")
                    print(f"Critical field {key} failed for product {product['sku']}, stopping product upload")
                    raise e
            counter += 1
            if counter == 4:
                cls.input_default(driver, product)
        match cls.get_mapped_key(product["category"]):
            case "Truien":
                fit_value = str(product.get("fit", "")).upper().strip()
                match fit_value:
                    case "SLIM FIT":
                        cls.change_maattabel(driver, "Profuomo slim fit truien")
                    case "NORMAL FIT":
                        cls.change_maattabel(driver, "Profuomo normal fit truien")
                    case "REGULAR FIT":
                        cls.change_maattabel(driver, "Profuomo regular fit truien")
                    case _:
                        cls.change_maattabel(driver, "Profuomo slim fit truien")

            case "Overhemden":
                fit_value = str(product.get("fit", "")).upper().strip()
                match fit_value:
                    case "RELAXED FIT":
                        cls.change_maattabel(driver, "Profuomo relaxed fit overhemden")
                    case "REGULAR FIT":
                        cls.change_maattabel(driver, "Profuomo regular fit overhemden")
                    case "SUPER SLIM FIT":
                        cls.change_maattabel(driver, "Profuomo super slim fit overhemden")
                    case _:
                        cls.change_maattabel(driver, "Profuomo slim fit overhemden")

            case "Overshirts":
                cls.change_maattabel(driver, "Profuomo overshirt normal fit")

            case "Polo's":
                cls.change_maattabel(driver, "Profuomo polo normal fit")

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
            if tries < 3:
                time.sleep(0.5)
            else:
                raise Exception("Sizes not added")

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
            csv_path = Path(PRODUCTS_PATH, "all.csv")
        products = pd.read_csv(csv_path, sep=",", quotechar='"')
        try:
            with Path(PRODUCTS_PATH, "done.txt").open(encoding="utf-8-sig") as f:
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
        input_csv_path = Path(PRODUCTS_PATH, "input.csv")
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
                if (input_csv := Path("input.csv")).exists():
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
                        if (input_csv := Path("input.csv")).exists():
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
        images_path = Path(PRODUCTS_PATH, sku)
        if not images_path.exists() or not images_path.is_dir():
            return False
        for image in images_path.glob("*"):
            if image.is_file():
                try:
                    if image.stat().st_size >= 1000:
                        return True
                except OSError:
                    continue
        return False

    @classmethod
    def register_products(
        cls, csv_path: str | None = None, test: bool = False
    ) -> dict[str, str]:
        cls._get_mapping()
        data = {"message": "", "error": ""}

        products = cls.get_products(csv_path)
        
        # Create debug log file
        debug_log_path = Path("error_debug.txt")
        with debug_log_path.open("w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("PRODUCT UPLOAD DEBUG LOG\n")
            f.write("=" * 80 + "\n")
            f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"CSV Path: {csv_path}\n")
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
            done_path = Path(PRODUCTS_PATH, "done.txt")
            failed_path = Path(PRODUCTS_PATH, "failed.txt")

            cls.go_to_product_catalogue(driver)

            cls.download_current_products(driver)

            products = cls.check_existing(driver, products, done_path)

            cls.go_to_form(driver)

            processed_count = 0
            success_count = 0
            failed_count = 0
            skipped_no_images_count = 0

            for _, product in filter(
                lambda product: product[1].get("Productnaam")
                and str(product[1].get("Productnaam")) != "nan",
                products.iterrows(),
            ):
                processed_count += 1
                sku = product["sku"]
                with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"PROCESSING PRODUCT: {sku}\n")
                    f.write(f"{'='*60}\n")
                    f.write(f"Product data: {dict(product)}\n")
                    f.write(f"Productnaam: {product.get('Productnaam', 'MISSING')}\n")
                    f.write(f"Category: {product.get('category', 'MISSING')}\n")
                if not cls.has_valid_images(sku):
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"SKIPPED: {sku} has no valid images in products/{sku}\n")
                    print(f"Skipping {sku}: no valid images found")
                    skipped_no_images_count += 1
                    continue
                
                try:
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
                        f.write(f"Starting register_product for {sku}\n")
                    cls.register_product(driver, product)
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
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
                    if (input_csv := Path("input.csv")).exists():
                        with input_csv.open(encoding="utf-8-sig") as existing_products:
                            lines.update(line.strip() for line in existing_products)

                    # Add the new product line to the set
                    lines.add(new_line)

                    # Write the unique lines back to the file
                    with input_csv.open("w", encoding="utf-8") as f:
                        f.write("\n".join(lines) + "\n")
                except Exception as e:
                    with Path("error_debug.txt").open("a", encoding="utf-8") as f:
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
                f"{skipped_no_images_count} skipped no images, {processed_count} processed)"
            )

        except Exception as ex:
            data["error"] = str(ex)
            data["message"] = ""

        finally:
            driver.quit()
            return data
