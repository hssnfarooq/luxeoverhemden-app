from functools import cache
import os
import re
import time
from typing import Any, Generator
import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait
from urllib.parse import urlparse

from automations.scraper import BaseScraper
from config import (
    PRODUCTS_PATH,
    PROFUOMO_PASSWORD,
    PROFUOMO_USERNAME,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from automations.openai_service import OpenAIService
from automations.template_service import TemplateService


class Profuomo(BaseScraper):
    @classmethod
    def profuomo_login(cls, driver: webdriver.Chrome):
        driver.get("https://b2b.profuomo.com/webstore/v2/login")
        cls.random_wait()
        # <input class="w3-input w3-border a4f-loginform-username" type="text" value="" name="username" id="id8" onchange="var wcall=wicketAjaxPost('/webstore/v2/login?3-1.IBehaviorListener.0-loginform-username', wicketSerialize(Wicket.$('id8')),function() { }.bind(this),function() { }.bind(this), function() {return Wicket.$('id8') != null;}.bind(this));">
        driver.find_element(By.NAME, "username").send_keys(PROFUOMO_USERNAME)
        cls.random_wait()
        # <input class="w3-input w3-border a4f-loginform-password" type="password" value="" name="password">
        driver.find_element(By.NAME, "password").send_keys(PROFUOMO_PASSWORD)
        cls.random_wait()
        # <button class="w3-btn w3-white w3-border a4f-loginform-submit" type="submit">Login</button>
        driver.find_element(By.CLASS_NAME, "a4f-loginform-submit").click()
        cls.random_wait()


class ProfuomoDownloader(Profuomo):
    @staticmethod
    def write_products_to_csv(filename, products):
        with open(filename, "w", encoding="utf-8") as file:
            file.write("ArtikelNr,Size,Quantity\n")
            for product in products:
                file.write(f"{product['id']},{product['size']},{product['stock']}\n")

    @staticmethod
    def fill_products(
        SKUs: list[dict[str, str | list[str]]],
        products: list[dict[str, str | list[str]]],
    ):
        # this function looks at the input SKUs and sets if the sku is not in products, adds them and set stock to 0
        for sku in SKUs:
            for size in sku["sizes"]:
                product = {"id": sku["sku"], "size": size, "stock": "0"}
                if not any(
                    p["id"] == product["id"] and p["size"] == product["size"]
                    for p in products
                ):
                    products.append(product)
        return products

    @staticmethod
    def sort_products(products: list):
        # sort products by id, then by size
        products.sort(key=lambda x: x["id"])
        # products.sort(key=lambda x: x['size'])
        return products

    @staticmethod
    def delete_csvs():
        if os.path.exists("profuomo_products.csv"):
            os.remove("profuomo_products.csv")
        # delete notfound.txt if exists
        if os.path.exists("notfound.txt"):
            os.remove("notfound.txt")

    @staticmethod
    def get_skus() -> list[dict[str, str | list[str]]]:
        SKUs = []
        with open("input.csv", "r", encoding="utf-8") as file:
            for line in file:
                # sample line: "PPUH10001A,37,38,39,40,41,42,43,44,45"
                # result wanted: { 'sku': 'PPUH10001A', 'sizes': [37,38,39,40,41,42,43,44,45] }
                line = line.strip().replace('"', "")
                line = line.split(",")
                sku = line[0]
                sizes = line[1:]
                SKUs.append({"sku": sku, "sizes": sizes})
        return SKUs

    @classmethod
    def search_sku(cls, driver: webdriver.Chrome, sku: str):
        try:
            # <button class="search_button"><i class="material-icons">search</i> Search </button>
            driver.find_element(By.CLASS_NAME, "search_button").click()
            cls.random_wait()
        except Exception:
            pass
        # <input type="text" name="q" value="" class="w3-input w3-border a4f-searchquery ui-autocomplete-input" autocomplete="off" placeholder=" Search ">
        driver.find_element(By.NAME, "q").send_keys(sku)
        time.sleep(5)
        try:
            # find by xpath //*[contains(@class,'ui-autocomplete')]/li[1]
            result = driver.find_element(
                By.XPATH, "//ul[contains(@class,'ui-autocomplete')]//a"
            )
            result.click()
        except Exception:
            print(f"SKU {sku} not found")
            # write SKU to notfound.txt
            with open("notfound.txt", "a+", encoding="utf-8") as file:
                file.write(f"{sku}\n")
            # clear search input
            driver.find_element(By.NAME, "q").clear()
            return False
        return True

    @classmethod
    def download_profuomo(cls, headless=False):
        status: dict[str, str | None] = {"message": None, "error": None}
        try:
            # delete products.csv if exists
            cls.delete_csvs()
            all_products = []
            SKUs = cls.get_skus()
            options = webdriver.ChromeOptions()
            if headless:
                options.add_argument("headless")
            driver = webdriver.Chrome(options=options)
            driver.implicitly_wait(10)
            # make driver full screen
            driver.maximize_window()

            cls.profuomo_login(driver)

            done_products = set()
            for sku in (p["sku"] for p in SKUs):
                if sku in done_products:
                    continue
                sku = str(sku)
                cls.random_wait()

                if not cls.search_sku(driver, sku):
                    continue

                cls.random_wait()
                # ! click on the search item

                # ! for testing purposes,
                # ! driver.get("https://b2b.profuomo.com/webstore/v2/product/Micro_Fashion_04/PPUH10001/M")
                try:
                    # wait until the elements exists: <div class="a4f-ordergrid-container">
                    WebDriverWait(driver, 10).until(
                        ec.presence_of_element_located(
                            (
                                By.CLASS_NAME,
                                "a4f-ordergrid-container",
                            )
                        )
                    )
                    # find all elements with a4f-ordergrid-orderline
                    products = driver.find_elements(
                        By.CLASS_NAME, "a4f-ordergrid-orderline"
                    )
                    len_products = len(products)
                except Exception:
                    print(f"Error: Could not load the product {sku}")
                    with open("notfound.txt", "a+", encoding="utf-8") as file:
                        file.write(f"{sku}\n")
                    continue

                # SO FAR SO GOOD!!

                for x in range(len_products):
                    product_id = None
                    product_size = None
                    product_stock = None
                    product_xpath_selector = (
                        "(//div[contains(@class,'a4f-ordergrid-orderline')])["
                        + str(x + 1)
                        + "]"
                    )
                    product = driver.find_element(By.XPATH, product_xpath_selector)
                    product_id_tmp = product.find_element(
                        By.XPATH,
                        product_xpath_selector
                        + "//a[contains(@class,'a4f-ordergrid-productinfo-link')]",
                    ).get_attribute("title")

                    if product_id_tmp in done_products:
                        continue
                    product_id = product_id_tmp
                    sizes_div = product.find_elements(By.CLASS_NAME, "og_size")
                    for size in sizes_div:
                        # print the classes, and then the xpath that lead to this element:
                        # print("classes: " + str(size.get_attribute("class")))
                        classes = size.get_attribute("class").split(" ")  # type: ignore
                        for class_name in classes:
                            if class_name.startswith("product_"):
                                product_size = class_name.split("_")[-1].lower()
                                # <div class="a4f-ordergrid-stockcount">1</div>
                        try:
                            size_xpath_temp = (
                                product_xpath_selector
                                + "//div[@class='"
                                + str(size.get_attribute("class"))
                                + "']//div[@class='wrap-ordergrid-quantity']/div"
                            )
                            print("size_xpath_temp: " + size_xpath_temp)
                            product_stock = product.find_element(
                                By.XPATH, size_xpath_temp
                            )
                            product_stock = product_stock.text
                            product_stock = product_stock.replace("100+", "99")
                        except Exception:
                            product_stock = "0"
                        if not product_id or not product_stock or not product_size:
                            print("Error: Could not find the product id, size or stock")
                            continue
                        product_row = {
                            "id": product_id,
                            "size": product_size.upper(),
                            "stock": product_stock,
                        }
                        print(product_row)
                        all_products.append(product_row)
                    done_products.add(product_id)
            driver.close()
            products = cls.fill_products(SKUs, all_products)
            products = cls.sort_products(products)
            cls.write_products_to_csv("profuomo_products.csv", products)
            status["message"] = "Finished"
        except Exception as ex:
            template = "er is een {0} opgetreden: {1!r}"
            status["error"] = template.format(type(ex).__name__, ex.args)
        finally:
            return status


class ProfuomoScraper(Profuomo):
    name_update_url = "https://profuomo.com/nl/sitemap-article-1.xml"
    # Use template service instead of expensive OpenAI
    template_service: TemplateService = TemplateService()
    
    # Keep OpenAI as backup (optional)
    openai_service: OpenAIService = OpenAIService(
        api_key=OPENAI_API_KEY,
        model=OPENAI_MODEL,
    )

    @staticmethod
    def get_all_products(
        driver: webdriver.Chrome,
    ) -> Generator[tuple[str, str], None, None]:
        products = driver.find_elements(By.CLASS_NAME, "card__product--name")
        for product in products:
            product_text = product.text
            parent_a_tag = product.find_element(By.XPATH, "./ancestor::a[1]")
            product_href = str(parent_a_tag.get_attribute("href"))
            yield product_text, product_href

    @classmethod
    def load_more(cls, driver: webdriver.Chrome) -> bool:
        # Scroll down to the bottom of the page
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        cls.random_wait()

        buttons = driver.find_elements(By.CLASS_NAME, "load-more")
        if buttons:
            try:
                buttons[0].click()
            except Exception:
                return False
            return True
        return False

    @staticmethod
    def get_done_ids() -> set[str]:
        done_ids = set()
        done_path = os.path.join(PRODUCTS_PATH, "all.csv")
        if os.path.exists(done_path):
            with open(done_path, "r", encoding="utf-8") as file:
                for line in file:
                    if line.startswith("sku"):
                        continue
                    done_ids.add(line.strip().split(",")[0])
        return done_ids

    @staticmethod
    def get_category(url: str) -> str:
        parsed = urlparse(url)
        return parsed.path.split("/")[-1].replace("%20", "_")

    @classmethod
    def save_products(cls, products: list[dict[str, Any]], category: str):
        category_path = os.path.join(PRODUCTS_PATH, f"{category.lower()}.csv")
        new = pd.DataFrame(products)
        updated = cls.update_names(new)
        if not os.path.exists(category_path):
            updated.to_csv(category_path, index=False)
        else:
            try:
                existing = pd.read_csv(category_path)
                pd.concat([existing, updated]).to_csv(category_path, index=False)
            except pd.errors.EmptyDataError:
                updated.to_csv(category_path, index=False)

    @staticmethod
    def get_product_name(driver: webdriver.Chrome) -> str:
        product_title_wrap = driver.find_element(By.CLASS_NAME, "product-title-wrap")
        product_name = product_title_wrap.find_element(By.TAG_NAME, "h1").text
        return product_name

    @staticmethod
    def get_product_details(driver: webdriver.Chrome) -> dict[str, Any]:
        details = {}
        table = driver.find_element(By.CLASS_NAME, "extra-fields")
        rows = table.find_elements(By.TAG_NAME, "tr")
        prices = driver.find_elements(
            By.XPATH,
            "//div[@class='product-price']//span[contains(@class,'a4f-price')]",
        )
        details["wsp"] = prices[0].text
        details["rrp"] = prices[-1].text

        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) == 2:
                key = tds[0].text.lower()
                value = tds[1].text
                details[key] = value
        return details

    @classmethod
    def get_product_sizes(cls, driver: webdriver.Chrome) -> list[str]:
        sizes = []

        try:
            try:
                pr_sizes_div = driver.find_element(By.CLASS_NAME, "pr-sizes")
            except Exception:
                # Scroll down a little
                driver.execute_script("window.scrollBy(0, 300);")
                cls.random_wait()
                pr_sizes_div = driver.find_element(By.CLASS_NAME, "pr-sizes")

            h_size_divs = pr_sizes_div.find_elements(By.CLASS_NAME, "h_size")

            for h_size_div in h_size_divs:
                size_name_span = h_size_div.find_element(By.CLASS_NAME, "size_name")
                sizes.append(size_name_span.text)
        except Exception:
            pass

        return sizes

    @classmethod
    def download_images(cls, driver: webdriver.Chrome, sku: str) -> int:
        # Create the folder for the SKU
        sku_folder = os.path.join(PRODUCTS_PATH, sku)
        os.makedirs(sku_folder, exist_ok=True)
        downloaded_count = 0

        # Find the images inside the a4f-images div
        images_div = driver.find_element(By.CLASS_NAME, "a4f-images")
        images = images_div.find_elements(By.TAG_NAME, "img")

        for index, img in enumerate(images):
            img_url = str(img.get_attribute("src"))
            img_data = requests.get(img_url).content
            img_path = os.path.join(sku_folder, f"{sku}_{index}.jpg")

            with open(img_path, "wb") as img_file:
                img_file.write(img_data)
            # check the size of the image, if it is less than 18,801 bytes, delete it and raise an error
            if os.path.getsize(img_path) < 18801:
                os.remove(img_path)
                print(f"⚠️ Warning: Image too small for {sku}_{index}.jpg, skipping...")
                continue  # Skip this image instead of failing the entire product
            downloaded_count += 1
        return downloaded_count

    @staticmethod
    def get_product_sku(driver: webdriver.Chrome) -> str:
        try:
            sku = driver.find_element(By.CLASS_NAME, "a4f-product-uniqueid").text
            return sku
        except Exception as e:
            print(f"⚠️ Warning: Could not find SKU element: {e}")
            # Try alternative selectors
            try:
                # Try to extract SKU from URL
                current_url = driver.current_url
                if "PPWF" in current_url:
                    import re
                    sku_match = re.search(r'/(PPWF\d+[A-Z]?)/', current_url)
                    if sku_match:
                        return sku_match.group(1)
                # Try other possible selectors
                sku = driver.find_element(By.CSS_SELECTOR, "[class*='product'][class*='id']").text
                return sku
            except Exception:
                raise Exception("Could not extract SKU from product page")

    @classmethod
    def scrape_product(
        cls, driver: webdriver.Chrome, url: str, category: str
    ) -> dict[str, Any]:
        driver.get(url)
        cls.random_wait()
        product = {}
        
        # Get SKU first - this is critical
        try:
            product["sku"] = cls.get_product_sku(driver)
        except Exception as e:
            raise Exception(f"Failed to get SKU: {e}")
        
        # Get other product details
        try:
            product["name"] = cls.get_product_name(driver)
            product["category"] = category
            product.update(cls.get_product_details(driver))
            product["sizes"] = cls.get_product_sizes(driver)
        except Exception as e:
            print(f"⚠️ Warning: Some product details failed for {product.get('sku', 'Unknown')}: {e}")
        
        # Download images (non-critical)
        image_count = 0
        try:
            image_count = cls.download_images(driver, product["sku"])
        except Exception as e:
            print(f"⚠️ Warning: Image download failed for {product.get('sku', 'Unknown')}: {e}")
            # Continue without images rather than failing the entire product
        product["image_count"] = image_count
        product["has_images"] = image_count > 0
        
        return product

    @classmethod
    def scrape_profuomo(cls, url: str):
        # ! Test this with https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Shirts
        # ! also with https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Sale/Shirts%20Profuomo?18
        data = {"message": "", "error": ""}
        options = webdriver.ChromeOptions()
        driver = webdriver.Chrome(options=options)
        try:
            driver.implicitly_wait(10)
            # make driver full screen
            driver.maximize_window()
            done_ids = cls.get_done_ids()
            required_ids: set[str] = set()
            required_links: list[str] = list()

            category = cls.get_category(url)

            cls.profuomo_login(driver)
            driver.get(url)

            cls.random_wait()

            while True:
                for product, link in cls.get_all_products(driver):
                    if product in done_ids or product in required_ids:
                        continue
                    required_ids.add(product)
                    required_links.append(link)
                if not cls.load_more(driver):
                    break
            products = []
            for i, link in enumerate(required_links):
                try:
                    # Check if driver is still alive
                    try:
                        driver.current_url
                    except Exception:
                        print("❌ Browser driver has crashed or is no longer responsive")
                        break
                    
                    print(f"Scraping product {i+1}/{len(required_links)}: {link}")
                    product = cls.scrape_product(driver, link, category)
                    if not product.get("has_images", False):
                        msg = (
                            f"Skipped {product.get('sku', 'Unknown SKU')}: "
                            "no valid supplier images found"
                        )
                        print(f"⚠️ {msg}")
                        with open("scraping_errors.log", "a", encoding="utf-8") as f:
                            f.write(msg + "\n")
                        continue
                    products.append(product)
                    print(f"✅ Successfully scraped: {product.get('sku', 'Unknown SKU')}")
                except Exception as e:
                    print(f"❌ Failed to scrape {link}: {str(e)}")
                    # Log the specific error
                    with open("scraping_errors.log", "a", encoding="utf-8") as f:
                        f.write(f"Failed to scrape {link}: {str(e)}\n")
                    # Continue with next product
                    continue
            cls.save_products(products, category)
            cls.save_products(products, "all")
            data["message"] = "Finished"
        except Exception as e:
            print(f"❌ Critical error in scrape_profuomo: {str(e)}")
            data["message"] = ""
            data["error"] = str(e)
            # Log the critical error
            with open("scraping_errors.log", "a", encoding="utf-8") as f:
                f.write(f"Critical error in scrape_profuomo: {str(e)}\n")
        finally:
            driver.quit()
            return data

    @classmethod
    def update_names(cls, df: pd.DataFrame) -> pd.DataFrame:
        done = set()
        for row in cls.gen_names_df():
            df.loc[df["sku"] == row["sku"], "Productnaam"] = row["Productnaam"]
            done.add(row["sku"])

        for _, row in df[~df["sku"].isin(done)].iterrows():
            naam = row.get("Productnaam")
            if not naam or str(naam) == "nan" or str(naam) == "NaN" or pd.isna(naam) or str(naam).endswith(" nan"):
                df.loc[df["sku"] == row["sku"], "Productnaam"] = cls.create_name(row)
        return df

    @classmethod
    def create_name(cls, row: pd.Series) -> str:
        color = row.get('color', '')
        collar = row.get('collar', '')
        category = row.get('category', '')
        
        # Special handling for overshirts: if collar is empty, use "overshirt"
        if category == 'Overshirts' and (not collar or collar == '' or str(collar) == 'nan'):
            collar = 'overshirt'
        
        if collar and collar != '' and str(collar) != 'nan':
            return f"Profuomo {color} {collar}".capitalize()
        else:
            return f"Profuomo {color}".capitalize()

    @classmethod
    def translate_to_dutch_with_openai(cls, text: str) -> str:
        # Use template service for translation (simple fallback)
        response = cls.template_service.translate_to_dutch(text)
        return response or ""

    @classmethod
    def gen_names_df(cls):
        for extracted_part in cls.extract_names_and_skus():
            *name, sku = extracted_part.split("-")
            name = " ".join(name)
            if not name.startswith("Profuomo"):
                name = f"Profuomo {name}"
            yield {"Productnaam": name, "sku": sku.upper()}

    @classmethod
    @cache
    def extract_names_and_skus(cls) -> list[str]:
        try:
            response = requests.get(cls.name_update_url, timeout=5)  # Reduced timeout
            return re.findall(
                r"https://profuomo\.com/nl/([^/]+)\.html",
                response.content.decode("utf-8"),
            )
        except Exception as e:
            print(f"Warning: Could not access sitemap for name updates: {e}")
            return []  # Return empty list if sitemap is not accessible
