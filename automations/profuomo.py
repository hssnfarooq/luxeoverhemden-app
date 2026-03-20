from functools import cache
import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Generator
from urllib.parse import parse_qs, urlparse, urljoin

import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

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
    LOGIN_URL = "https://b2b.profuomo.com/webstore/v2/login"
    SKU_REGEX = re.compile(r"\b(PP[A-Z0-9]{5,})\b", re.IGNORECASE)
    SIZE_REGEX = re.compile(r"^(?:\d{2,3}[A-Z]?|XS|S|M|L|XL|XXL|XXXL)$")
    NO_RESULTS_TOKENS: tuple[str, ...] = (
        "no results",
        "no result",
        "no products found",
        "nothing found",
        "geen resultaten",
        "geen resultaat",
        "0 results",
    )
    MIN_IMAGE_BYTES = int(os.getenv("PROFUOMO_MIN_IMAGE_BYTES", "5000"))
    STOCK_RETRY_COUNT = int(os.getenv("PROFUOMO_STOCK_RETRY_COUNT", "2"))
    INTER_SKU_DELAY_SECONDS = float(os.getenv("PROFUOMO_INTER_SKU_DELAY_SECONDS", "0.05"))
    SEARCH_INPUT_TIMEOUT_SECONDS = float(os.getenv("PROFUOMO_SEARCH_INPUT_TIMEOUT_SECONDS", "4"))
    SEARCH_RESULT_TIMEOUT_SECONDS = float(os.getenv("PROFUOMO_SEARCH_RESULT_TIMEOUT_SECONDS", "3"))
    ORDER_GRID_WAIT_LOOPS = int(os.getenv("PROFUOMO_ORDER_GRID_WAIT_LOOPS", "12"))
    ORDER_GRID_WAIT_INTERVAL_SECONDS = float(
        os.getenv("PROFUOMO_ORDER_GRID_WAIT_INTERVAL_SECONDS", "0.35")
    )
    DEBUG_CAPTURE = os.getenv("PROFUOMO_DEBUG_CAPTURE", "false").lower() == "true"
    DEBUG_CAPTURE_DIR = Path(os.getenv("PROFUOMO_DEBUG_DIR", "profuomo_debug"))

    LOGIN_USERNAME_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.NAME, "username"),
        (By.ID, "username"),
        (By.CSS_SELECTOR, "input[name*='user']"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[type='text']"),
    )
    LOGIN_PASSWORD_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.NAME, "password"),
        (By.ID, "password"),
        (By.CSS_SELECTOR, "input[type='password']"),
    )
    LOGIN_SUBMIT_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CLASS_NAME, "a4f-loginform-submit"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//button[contains(., 'Login') or contains(., 'Sign in')]"),
        (By.XPATH, "//input[@type='submit']"),
    )
    SEARCH_BUTTON_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CLASS_NAME, "search_button"),
        (By.CSS_SELECTOR, "button.search_button"),
        (By.CSS_SELECTOR, "button[class*='search']"),
        (By.XPATH, "//button[contains(., 'Search') or contains(., 'Zoek')]"),
    )
    SEARCH_INPUT_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.NAME, "q"),
        (By.CSS_SELECTOR, "input[name='q']"),
        (By.CSS_SELECTOR, "input[type='search']"),
        (By.CSS_SELECTOR, "input[class*='search']"),
        (By.CSS_SELECTOR, "input[placeholder*='Search']"),
    )
    SEARCH_RESULT_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.XPATH, "//ul[contains(@class,'ui-autocomplete')]//a"),
        (By.XPATH, "//li[contains(@class,'autocomplete')]//a"),
        (By.XPATH, "//a[contains(@href,'/product/')]"),
    )

    @classmethod
    def _get_local_chromedriver_paths(cls) -> list[Path]:
        root = Path.home() / ".cache" / "selenium" / "chromedriver"
        if not root.exists():
            return []
        paths = [p for p in root.rglob("chromedriver") if p.is_file()]
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return paths

    @classmethod
    def _create_chrome_driver(cls, options: webdriver.ChromeOptions) -> webdriver.Chrome:
        last_error: Exception | None = None

        for driver_path in cls._get_local_chromedriver_paths():
            try:
                service = Service(executable_path=str(driver_path))
                return webdriver.Chrome(service=service, options=options)
            except Exception as ex:
                last_error = ex

        try:
            return webdriver.Chrome(options=options)
        except Exception as ex:
            if last_error is not None:
                raise Exception(
                    f"Could not start Chrome driver (cached + fallback failed): {last_error}; {ex}"
                ) from ex
            raise

    @classmethod
    def _find_first(
        cls,
        root: webdriver.Chrome | Any,
        selectors: tuple[tuple[str, str], ...],
        timeout: float = 0,
        clickable: bool = False,
    ):
        deadline = time.monotonic() + timeout
        first_round = True

        while True:
            for by, selector in selectors:
                try:
                    element = root.find_element(by, selector)
                    if clickable and (not element.is_displayed() or not element.is_enabled()):
                        continue
                    return element
                except Exception:
                    continue

            if timeout <= 0:
                return None
            if not first_round and time.monotonic() >= deadline:
                return None
            first_round = False
            time.sleep(0.25)

    @classmethod
    def _find_all(
        cls,
        root: webdriver.Chrome | Any,
        selectors: tuple[tuple[str, str], ...],
    ) -> list[Any]:
        results: list[Any] = []
        seen: set[str] = set()
        for by, selector in selectors:
            try:
                elements = root.find_elements(by, selector)
            except Exception:
                continue
            for element in elements:
                key = getattr(element, "id", None) or f"{by}:{selector}:{len(results)}"
                if key in seen:
                    continue
                seen.add(key)
                results.append(element)
        return results

    @staticmethod
    def _clean_text(text: str | None) -> str:
        return (text or "").strip()

    @classmethod
    def _normalize_stock(cls, stock_text: str | None) -> str:
        text = cls._clean_text(stock_text)
        if not text:
            return "0"
        if "100+" in text:
            return "99"
        match = re.search(r"\d+", text)
        return match.group(0) if match else "0"

    @classmethod
    def _normalize_size(cls, size_text: str | None) -> str:
        text = cls._clean_text(size_text).upper()
        if not text:
            return ""
        text = text.replace("SIZE", "").replace("MAAT", "").strip()
        if cls.SIZE_REGEX.match(text):
            return text
        match = re.search(r"\b(XXXL|XXL|XL|L|M|S|XS|\d{2,3}[A-Z]?)\b", text)
        return match.group(1).upper() if match else ""

    @classmethod
    def _extract_sku_from_text(cls, text: str | None) -> str:
        candidate_text = cls._clean_text(text).upper()
        if not candidate_text:
            return ""
        match = cls.SKU_REGEX.search(candidate_text)
        return match.group(1).upper() if match else ""

    @classmethod
    def _extract_sku_from_url(cls, url: str | None) -> str:
        value = cls._clean_text(url).upper()
        if not value:
            return ""
        match = cls.SKU_REGEX.search(value)
        return match.group(1).upper() if match else ""

    @classmethod
    def _sku_base(cls, sku: str | None) -> str:
        value = cls._clean_text(sku).upper()
        if not value:
            return ""
        if len(value) >= 2 and value[-1].isalpha():
            return value[:-1]
        return value

    @classmethod
    def _sku_matches_expected(cls, candidate_sku: str | None, expected_sku: str | None) -> bool:
        candidate = cls._clean_text(candidate_sku).upper()
        expected = cls._clean_text(expected_sku).upper()
        if not candidate or not expected:
            return False
        if candidate == expected:
            return True
        return cls._sku_base(candidate) == cls._sku_base(expected)

    @classmethod
    def _normalize_candidate_image_url(cls, raw_url: str | None) -> str:
        value = cls._clean_text(raw_url)
        if not value:
            return ""
        value = html.unescape(value)
        value = value.replace("\\/", "/")
        value = value.replace("&amp;", "&")
        value = value.strip().strip("\"'`")
        if value.lower().startswith("url("):
            value = value[4:]
        value = value.strip("()\"'` ")
        for marker in ("&quot;", "\"", "'", ")", ";", ","):
            idx = value.find(marker)
            if idx != -1:
                value = value[:idx]
        value = value.rstrip(")]};,")
        if value.startswith("//"):
            value = f"https:{value}"
        return value.strip()

    @classmethod
    def _append_not_found(cls, sku: str):
        sku_value = cls._clean_text(sku).upper()
        if not sku_value:
            return
        existing: set[str] = set()
        if os.path.exists("notfound.txt"):
            with open("notfound.txt", "r", encoding="utf-8") as file:
                existing = {line.strip().upper() for line in file if line.strip()}
        if sku_value in existing:
            return
        with open("notfound.txt", "a+", encoding="utf-8") as file:
            file.write(f"{sku_value}\n")

    @classmethod
    def _capture_debug_artifacts(cls, driver: webdriver.Chrome, label: str):
        if not cls.DEBUG_CAPTURE:
            return
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label).strip("_") or "page"
        cls.DEBUG_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        html_path = cls.DEBUG_CAPTURE_DIR / f"{timestamp}_{safe_label}.html"
        png_path = cls.DEBUG_CAPTURE_DIR / f"{timestamp}_{safe_label}.png"
        try:
            html_path.write_text(driver.page_source, encoding="utf-8")
        except Exception:
            pass
        try:
            driver.save_screenshot(str(png_path))
        except Exception:
            pass

    @classmethod
    def _page_has_login_form(cls, driver: webdriver.Chrome) -> bool:
        try:
            body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text).lower()
        except Exception:
            return False
        has_login_words = ("username" in body_text and "password" in body_text) and (
            "sign in" in body_text or "reset password" in body_text or "activate" in body_text
        )
        has_password_input = (
            cls._find_first(driver, ((By.CSS_SELECTOR, "input[type='password']"),), timeout=0)
            is not None
        )
        return has_login_words and has_password_input

    @classmethod
    def _page_has_authenticated_hint(cls, driver: webdriver.Chrome) -> bool:
        try:
            body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text).lower()
        except Exception:
            body_text = ""
        success_tokens = (
            "choose a collection",
            "kies een collectie",
            "kollektion",
            "reorder",
            "watch video",
            "order management",
            "products",
        )
        return any(token in body_text for token in success_tokens)

    @classmethod
    def _page_has_no_results_hint(cls, driver: webdriver.Chrome) -> bool:
        try:
            body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text).lower()
        except Exception:
            return False
        return any(token in body_text for token in cls.NO_RESULTS_TOKENS)

    @classmethod
    def profuomo_login(cls, driver: webdriver.Chrome):
        driver.get(cls.LOGIN_URL)
        cls.random_wait()

        username_input = cls._find_first(driver, cls.LOGIN_USERNAME_SELECTORS, timeout=12)
        password_input = cls._find_first(driver, cls.LOGIN_PASSWORD_SELECTORS, timeout=12)
        if username_input is None or password_input is None:
            raise Exception("Could not locate Profuomo login form fields")

        username_input.clear()
        username_input.send_keys(PROFUOMO_USERNAME)
        cls.random_wait()

        password_input.clear()
        password_input.send_keys(PROFUOMO_PASSWORD)
        cls.random_wait()

        submit_button = cls._find_first(
            driver, cls.LOGIN_SUBMIT_SELECTORS, timeout=4, clickable=True
        )
        if submit_button is not None:
            submit_button.click()
        else:
            password_input.send_keys(Keys.ENTER)

        for _ in range(40):
            cls.random_wait()
            if cls._page_has_authenticated_hint(driver):
                return
            if not cls._page_has_login_form(driver):
                return

        if cls._page_has_login_form(driver):
            cls._capture_debug_artifacts(driver, "login_failed")
            raise Exception("Profuomo login failed or login page layout changed")


class ProfuomoDownloader(Profuomo):
    PRODUCTS_URL = "https://b2b.profuomo.com/products/Micro_Fashion_04"
    STOCK_ROW_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".a4f-ordergrid-orderline"),
        (By.CSS_SELECTOR, "[class*='ordergrid'][class*='orderline']"),
        (By.CSS_SELECTOR, "[data-sku]"),
    )
    STOCK_SIZE_BLOCK_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".og_size"),
        (By.CSS_SELECTOR, "[class*='og_size']"),
        (By.CSS_SELECTOR, "[data-size]"),
    )
    STOCK_VALUE_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".a4f-ordergrid-stockcount"),
        (By.CSS_SELECTOR, ".wrap-ordergrid-quantity div"),
        (By.CSS_SELECTOR, "[class*='stock']"),
        (By.CSS_SELECTOR, "[class*='quantity']"),
    )
    ORDER_GRID_PRODUCT_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".c-order-grid-product"),
        (By.CSS_SELECTOR, "[class*='order-grid-product']"),
    )
    ORDER_GRID_SIZE_ROW_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".c-order-grid-mobile-content-row__skus"),
        (By.CSS_SELECTOR, "[class*='content-row__skus']"),
    )
    ORDER_GRID_SIZE_TITLE_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, "p.c-order-grid-mobile-content-row__skus-title"),
        (By.CSS_SELECTOR, "[class*='skus-title']"),
    )
    ORDER_GRID_QTY_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".available-quantity__quantity"),
        (By.CSS_SELECTOR, "[class*='available-quantity__quantity']"),
        (By.CSS_SELECTOR, "[class*='stock-level']"),
    )

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
        products.sort(key=lambda x: (x["id"], x["size"]))
        return products

    @staticmethod
    def delete_csvs():
        if os.path.exists("profuomo_products.csv"):
            os.remove("profuomo_products.csv")
        if os.path.exists("notfound.txt"):
            os.remove("notfound.txt")

    @staticmethod
    def get_skus() -> list[dict[str, str | list[str]]]:
        skus = []
        with open("input.csv", "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip().replace('"', "")
                parts = line.split(",")
                if not parts or not parts[0]:
                    continue
                sku = parts[0].strip().upper()
                sizes = [size.strip().upper() for size in parts[1:] if size.strip()]
                skus.append({"sku": sku, "sizes": sizes})
        return skus

    @classmethod
    def search_sku(cls, driver: webdriver.Chrome, sku: str) -> bool:
        if cls._search_sku_new_flow(driver, sku):
            return True
        if cls._page_has_no_results_hint(driver):
            return False
        return cls._search_sku_legacy_flow(driver, sku)

    @classmethod
    def _search_sku_new_flow(cls, driver: webdriver.Chrome, sku: str) -> bool:
        try:
            driver.get(cls.PRODUCTS_URL)
            search_input = cls._find_first(
                driver,
                (
                    (By.ID, "productSearch"),
                    (By.NAME, "productSearch"),
                ),
                timeout=cls.SEARCH_INPUT_TIMEOUT_SECONDS,
            )
            if search_input is None:
                return False

            try:
                search_input.clear()
            except Exception:
                pass
            search_input.send_keys(sku)
            result_title = None
            deadline = time.monotonic() + cls.SEARCH_RESULT_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                result_title = cls._find_first(
                    driver,
                    (
                        (
                            By.XPATH,
                            f"//*[contains(@class,'product-card__title') and "
                            f"contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku}')]",
                        ),
                    ),
                    timeout=0,
                    clickable=True,
                )
                if result_title is not None:
                    break
                if cls._page_has_no_results_hint(driver):
                    return False
                time.sleep(0.2)
            if result_title is None:
                return False

            try:
                result_title.click()
            except Exception:
                driver.execute_script("arguments[0].click();", result_title)
            time.sleep(0.35)

            clicked_order_grid = False
            for _ in range(cls.ORDER_GRID_WAIT_LOOPS):
                body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text).lower()
                if "deliveries" in body_text:
                    return True

                order_grid_button = cls._find_first(
                    driver,
                    (
                        (
                            By.XPATH,
                            "//*[self::button or self::a][contains(.,'Go to Order Grid') or contains(.,'Order Grid') or contains(.,'Bestelraster')]",
                        ),
                    ),
                    timeout=0,
                    clickable=False,
                )
                if order_grid_button is not None and not clicked_order_grid:
                    try:
                        driver.execute_script("arguments[0].click();", order_grid_button)
                    except Exception:
                        try:
                            order_grid_button.click()
                        except Exception:
                            pass
                    clicked_order_grid = True
                time.sleep(cls.ORDER_GRID_WAIT_INTERVAL_SECONDS)

            body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text).lower()
            if "order grid" in body_text or "deliveries" in body_text:
                return True
            return sku in driver.current_url.upper() or sku in body_text.upper()
        except Exception:
            return False

    @classmethod
    def _search_sku_legacy_flow(cls, driver: webdriver.Chrome, sku: str) -> bool:
        search_button = cls._find_first(driver, cls.SEARCH_BUTTON_SELECTORS, timeout=2)
        if search_button is not None:
            try:
                search_button.click()
                cls.random_wait()
            except Exception:
                pass

        search_input = cls._find_first(
            driver,
            cls.SEARCH_INPUT_SELECTORS,
            timeout=cls.SEARCH_INPUT_TIMEOUT_SECONDS,
        )
        if search_input is None:
            print(f"SKU {sku} not found: search input missing")
            cls._capture_debug_artifacts(driver, f"search_input_missing_{sku}")
            return False

        try:
            search_input.clear()
        except Exception:
            pass
        search_input.send_keys(sku)
        time.sleep(0.4)
        if cls._page_has_no_results_hint(driver):
            return False

        result_link = cls._find_first(
            driver,
            cls.SEARCH_RESULT_SELECTORS,
            timeout=cls.SEARCH_RESULT_TIMEOUT_SECONDS,
            clickable=True,
        )
        if result_link is not None:
            try:
                result_link.click()
                cls.random_wait()
                return True
            except Exception:
                pass

        try:
            candidate_links = driver.find_elements(
                By.XPATH,
                f"//a[contains(@href,'{sku}') or contains(translate(normalize-space(.),"
                f"'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku}')]",
            )
            if candidate_links:
                candidate_links[0].click()
                cls.random_wait()
                return True
        except Exception:
            pass

        previous_url = driver.current_url
        search_input.send_keys(Keys.ENTER)
        time.sleep(0.35)
        if driver.current_url != previous_url:
            return True
        if sku in driver.current_url.upper() or "/product/" in driver.current_url.lower():
            return True

        print(f"SKU {sku} not found")
        cls._capture_debug_artifacts(driver, f"search_not_found_{sku}")
        try:
            search_input.clear()
        except Exception:
            pass
        return False

    @classmethod
    def _extract_product_id_from_container(cls, container: Any, fallback_sku: str) -> str:
        attr_candidates = ("data-sku", "data-product-sku", "data-product", "data-id")
        for attr in attr_candidates:
            try:
                value = container.get_attribute(attr)
            except Exception:
                continue
            sku = cls._extract_sku_from_text(value)
            if sku:
                return sku

        selectors: tuple[tuple[str, str], ...] = (
            (By.CSS_SELECTOR, ".a4f-ordergrid-productinfo-link"),
            (By.CSS_SELECTOR, "a[href*='/product/']"),
            (By.CSS_SELECTOR, "a"),
        )
        link = cls._find_first(container, selectors)
        if link is not None:
            for attr in ("title", "data-sku", "href"):
                try:
                    value = link.get_attribute(attr)
                except Exception:
                    value = ""
                sku = cls._extract_sku_from_text(value) or cls._extract_sku_from_url(value)
                if sku:
                    return sku
            sku_from_text = cls._extract_sku_from_text(link.text)
            if sku_from_text:
                return sku_from_text

        container_sku = cls._extract_sku_from_text(getattr(container, "text", ""))
        return container_sku or fallback_sku.upper()

    @classmethod
    def _extract_stock_rows_from_containers(
        cls, driver: webdriver.Chrome, fallback_sku: str
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        containers = cls._find_all(driver, cls.STOCK_ROW_SELECTORS)

        for container in containers:
            product_id = cls._extract_product_id_from_container(container, fallback_sku)
            size_blocks = cls._find_all(container, cls.STOCK_SIZE_BLOCK_SELECTORS)
            if not size_blocks:
                continue

            for size_block in size_blocks:
                size = cls._normalize_size(size_block.get_attribute("data-size"))
                if not size:
                    classes = cls._clean_text(size_block.get_attribute("class"))
                    class_match = re.search(r"product_([A-Za-z0-9]+)", classes)
                    if class_match:
                        size = cls._normalize_size(class_match.group(1))
                if not size:
                    size = cls._normalize_size(size_block.text)
                if not size:
                    continue

                stock = cls._normalize_stock(size_block.get_attribute("data-stock"))
                if stock == "0":
                    stock = cls._normalize_stock(size_block.get_attribute("data-quantity"))
                if stock == "0":
                    stock_node = cls._find_first(size_block, cls.STOCK_VALUE_SELECTORS)
                    if stock_node is not None:
                        stock = cls._normalize_stock(stock_node.text)

                key = (product_id, size)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"id": product_id, "size": size, "stock": stock})

        return rows

    @classmethod
    def _extract_stock_rows_from_order_grid(
        cls, driver: webdriver.Chrome, fallback_sku: str
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text).lower()
        if "order grid" not in body_text and "deliveries" not in body_text:
            return rows

        extracted = driver.execute_script(
            """
            const skuRegex = /PP[A-Z0-9]{5,}/i;
            const out = [];
            const productBlocks = Array.from(
              document.querySelectorAll('.c-order-grid-product, [class*="order-grid-product"]')
            );
            for (const block of productBlocks.slice(0, 60)) {
              const blockText = (block.innerText || '').replace(/\\s+/g, ' ').trim();
              const skuMatch = blockText.match(skuRegex);
              const sku = skuMatch ? skuMatch[0].toUpperCase() : '';
              const sizeRows = Array.from(
                block.querySelectorAll('.c-order-grid-mobile-content-row__skus, [class*="content-row__skus"]')
              );
              for (const row of sizeRows.slice(0, 80)) {
                const sizeNode = row.querySelector('p.c-order-grid-mobile-content-row__skus-title, [class*="skus-title"]');
                const qtyNode = row.querySelector(
                  '.available-quantity__quantity, [class*="available-quantity__quantity"], [class*="stock-level"]'
                );
                out.push({
                  id: sku,
                  size: sizeNode ? (sizeNode.innerText || '') : '',
                  stock: qtyNode ? (qtyNode.innerText || '') : (row.innerText || ''),
                });
              }
            }
            return out;
            """
        )

        if not extracted:
            extracted = driver.execute_script(
                """
                const skuRegex = /PP[A-Z0-9]{5,}/i;
                const out = [];
                const gridRows = Array.from(document.querySelectorAll('.template-order-grid-row'));
                if (!gridRows.length) {
                  return out;
                }

                const headerRow = gridRows.find((row) => row.classList.contains('order-grid-header-row')) || gridRows[0];
                const headerSizesText = (
                  headerRow.querySelector('.template-order-grid-row__items')?.innerText || ''
                ).replace(/\\s+/g, ' ').trim();
                const headerSizes = headerSizesText
                  .split(' ')
                  .map((s) => s.trim())
                  .filter(Boolean);

                const productRows = gridRows.filter((row) => row.classList.contains('c-order-grid-row-manual'));
                for (const row of productRows.slice(0, 120)) {
                  const rowText = (row.innerText || '').replace(/\\s+/g, ' ').trim();
                  const skuMatch = rowText.match(skuRegex);
                  const sku = skuMatch ? skuMatch[0].toUpperCase() : '';
                  const stockCells = Array.from(
                    row.querySelectorAll('.template-order-grid-row__items .order-grid-sku')
                  );
                  stockCells.forEach((cell, index) => {
                    const qtyNode = cell.querySelector(
                      '.available-quantity__quantity, .available-quantity__stock-level, [class*=\"available-quantity\"]'
                    );
                    const stockText = qtyNode ? (qtyNode.innerText || '') : (cell.innerText || '');
                    out.push({
                      id: sku,
                      size: headerSizes[index] || '',
                      stock: stockText,
                    });
                  });
                }
                return out;
                """
            )

        for row in extracted or []:
            product_id = cls._extract_sku_from_text(row.get("id")) or fallback_sku.upper()
            size = cls._normalize_size(str(row.get("size", "")))
            stock = cls._normalize_stock(str(row.get("stock", "")))
            if not size:
                continue
            key = (product_id, size)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"id": product_id, "size": size, "stock": stock})

        return rows

    @classmethod
    def _extract_stock_rows_from_source(
        cls, driver: webdriver.Chrome, fallback_sku: str
    ) -> list[dict[str, str]]:
        source = driver.page_source
        rows: list[dict[str, str]] = []
        seen_sizes: set[str] = set()

        size_pattern = r"(?:\d{2,3}[A-Za-z]?|XS|S|M|L|XL|XXL|XXXL)"
        pattern_one = re.compile(
            rf'"(?:size|maat|label)"\s*:\s*"(?P<size>{size_pattern})"'
            rf'.{{0,160}}?"(?:stock|quantity|available|voorraad)"\s*:\s*"?(?P<qty>\d+|100\+)',
            re.IGNORECASE | re.DOTALL,
        )
        pattern_two = re.compile(
            rf'"(?:stock|quantity|available|voorraad)"\s*:\s*"?(?P<qty>\d+|100\+)"?'
            rf'.{{0,160}}?"(?:size|maat|label)"\s*:\s*"(?P<size>{size_pattern})"',
            re.IGNORECASE | re.DOTALL,
        )

        for match in list(pattern_one.finditer(source)) + list(pattern_two.finditer(source)):
            size = cls._normalize_size(match.group("size"))
            if not size or size in seen_sizes:
                continue
            seen_sizes.add(size)
            rows.append(
                {
                    "id": fallback_sku.upper(),
                    "size": size,
                    "stock": cls._normalize_stock(match.group("qty")),
                }
            )
        return rows

    @classmethod
    def extract_stock_rows(cls, driver: webdriver.Chrome, fallback_sku: str) -> list[dict[str, str]]:
        rows = cls._extract_stock_rows_from_order_grid(driver, fallback_sku)
        if rows:
            return rows

        order_grid_button = cls._find_first(
            driver,
            (
                (
                    By.XPATH,
                    "//*[self::button or self::a][contains(.,'Go to Order Grid') or contains(.,'Order Grid') or contains(.,'Bestelraster')]",
                ),
            ),
            timeout=2,
            clickable=False,
        )
        if order_grid_button is not None:
            try:
                driver.execute_script("arguments[0].click();", order_grid_button)
            except Exception:
                try:
                    order_grid_button.click()
                except Exception:
                    pass
            for _ in range(8):
                time.sleep(0.25)
                rows = cls._extract_stock_rows_from_order_grid(driver, fallback_sku)
                if rows:
                    return rows

        if "/products/" in driver.current_url and "/webstore/v2" not in driver.current_url:
            for _ in range(12):
                time.sleep(0.25)
                rows = cls._extract_stock_rows_from_order_grid(driver, fallback_sku)
                if rows:
                    return rows

        rows = cls._extract_stock_rows_from_source(driver, fallback_sku)
        if rows:
            return rows

        page_source_l = driver.page_source.lower()
        legacy_markers = ("a4f-ordergrid", "a4f-ordergrid-orderline", "a4f-product-uniqueid")
        if not any(marker in page_source_l for marker in legacy_markers):
            return []

        rows = cls._extract_stock_rows_from_containers(driver, fallback_sku)
        if rows:
            return rows
        return cls._extract_stock_rows_from_source(driver, fallback_sku)

    @classmethod
    def download_profuomo(cls, headless=False):
        status: dict[str, str | None] = {"message": None, "error": None}
        driver: webdriver.Chrome | None = None
        try:
            cls.delete_csvs()
            all_products: list[dict[str, str]] = []
            skus = cls.get_skus()
            options = webdriver.ChromeOptions()
            if headless:
                options.add_argument("headless")
            driver = cls._create_chrome_driver(options)
            # Use explicit waits in helper methods; implicit waits make missing selectors very slow.
            driver.implicitly_wait(0)
            driver.maximize_window()

            cls.profuomo_login(driver)

            processed_skus: set[str] = set()
            for sku in (str(p["sku"]).upper() for p in skus):
                if sku in processed_skus:
                    continue
                processed_skus.add(sku)

                rows: list[dict[str, str]] = []
                found_product = False
                for _ in range(max(1, cls.STOCK_RETRY_COUNT)):
                    time.sleep(cls.INTER_SKU_DELAY_SECONDS)
                    if not cls.search_sku(driver, sku):
                        # Product not found in search: do not retry, move on immediately.
                        break
                    found_product = True
                    time.sleep(cls.INTER_SKU_DELAY_SECONDS)
                    rows = cls.extract_stock_rows(driver, sku)
                    if rows:
                        break
                if not rows:
                    if found_product:
                        print(f"Error: Found SKU {sku} but could not extract stock rows")
                    else:
                        print(f"Error: Could not find SKU {sku}")
                        cls._append_not_found(sku)
                    continue

                all_products.extend(rows)

            if not all_products:
                raise Exception(
                    "No stock rows extracted from Profuomo. "
                    "CSV generation aborted to prevent uploading zeroed stock."
                )

            if driver is not None:
                driver.close()
                driver = None

            products = cls.fill_products(skus, all_products)
            products = cls.sort_products(products)
            cls.write_products_to_csv("profuomo_products.csv", products)
            status["message"] = "Finished"
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


class ProfuomoScraper(Profuomo):
    name_update_url = "https://profuomo.com/nl/sitemap-article-1.xml"
    template_service: TemplateService = TemplateService()
    openai_service: OpenAIService = OpenAIService(
        api_key=OPENAI_API_KEY,
        model=OPENAI_MODEL,
    )

    PRODUCT_LINK_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, "a[href*='/webstore/v2/product/']"),
        (By.CSS_SELECTOR, "a[href*='/products/']"),
        (By.CSS_SELECTOR, "a[href*='/product/']"),
    )
    PRODUCT_CARD_TITLE_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".product-card__title"),
        (By.CSS_SELECTOR, "[class*='product-card__title']"),
    )
    PRODUCT_CARD_SHOW_MORE_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".product-card__show-more-button"),
        (By.CSS_SELECTOR, "[class*='product-card__show-more-button']"),
    )
    LOAD_MORE_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CLASS_NAME, "load-more"),
        (By.CSS_SELECTOR, "button.load-more"),
        (By.CSS_SELECTOR, ".products-page__load-more button"),
        (By.XPATH, "//button[contains(., 'Load more') or contains(., 'Meer')]"),
        (
            By.XPATH,
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'show more products')]",
        ),
        (
            By.XPATH,
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more products')]",
        ),
    )
    PRODUCT_NAME_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".product-title-wrap h1"),
        (By.CSS_SELECTOR, "[class*='product-details'] h1"),
        (By.CSS_SELECTOR, "[class*='product-details'] h2"),
        (By.CSS_SELECTOR, "[class*='product-details'] h3"),
        (By.CSS_SELECTOR, "h1"),
        (By.CSS_SELECTOR, "[class*='product-title']"),
        (By.CSS_SELECTOR, "[class*='title'] h1"),
    )
    SIZE_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".pr-sizes .size_name"),
        (By.CSS_SELECTOR, ".pr-sizes .h_size"),
        (By.CSS_SELECTOR, "[data-size]"),
        (By.CSS_SELECTOR, "button[class*='size']"),
        (By.CSS_SELECTOR, "[class*='size']"),
    )
    IMAGE_SELECTORS: tuple[tuple[str, str], ...] = (
        (By.CSS_SELECTOR, ".a4f-images img"),
        (By.CSS_SELECTOR, ".product-gallery img"),
        (By.CSS_SELECTOR, "[class*='gallery'] img"),
        (By.CSS_SELECTOR, "[class*='product-details'] img"),
    )
    DETAIL_KEY_ALIASES: dict[str, str] = {
        "material": "material",
        "materials": "material",
        "color": "color",
        "colour": "color",
        "kleur": "color",
        "stock label": "stocklabel",
        "stocklabel": "stocklabel",
        "available from": "stocklabel",
        "design": "design",
        "capsule": "capsule",
        "fabric composition": "fabriccomp",
        "fabriccomp": "fabriccomp",
        "fit": "fit",
        "fabric": "fabric",
        "quality": "quality",
        "description": "description",
        "cuff": "cuff",
        "cuffs": "cuff",
        "manchet": "cuff",
        "sleeve": "sleeve",
        "sleeve length": "sleeve",
        "sleevelength": "sleeve",
        "mouwlengte": "sleeve",
        "collar": "collar",
        "kraag": "collar",
        "sustainability": "sustainability",
        "iconen": "sustainability",
        "icons": "sustainability",
        "easy care": "noniron",
        "non iron": "noniron",
        "noniron": "noniron",
        "item comment": "item comment smc",
        "item comment smc": "item comment smc",
    }

    @staticmethod
    def get_all_products(
        driver: webdriver.Chrome,
    ) -> Generator[tuple[str, str], None, None]:
        seen_links: set[str] = set()
        yielded = False
        for anchor in Profuomo._find_all(driver, ProfuomoScraper.PRODUCT_LINK_SELECTORS):
            href = Profuomo._clean_text(anchor.get_attribute("href"))
            if not href or ("/product/" not in href and "/products/" not in href):
                continue
            if href in seen_links:
                continue
            seen_links.add(href)
            yielded = True
            product_text = Profuomo._clean_text(anchor.text)
            if not product_text:
                product_text = (
                    Profuomo._clean_text(anchor.get_attribute("title"))
                    or Profuomo._clean_text(anchor.get_attribute("aria-label"))
                )
            if not product_text:
                product_text = href.rstrip("/").split("/")[-1]
            yield product_text, href

        if yielded:
            return

        seen_skus: set[str] = set()
        for title_node in Profuomo._find_all(driver, ProfuomoScraper.PRODUCT_CARD_TITLE_SELECTORS):
            title_text = Profuomo._clean_text(title_node.text)
            sku = Profuomo._extract_sku_from_text(title_text) or title_text.upper()
            if not sku or sku in seen_skus:
                continue
            seen_skus.add(sku)
            href = ProfuomoScraper._extract_card_href_for_title_node(driver, title_node)
            if href:
                yield sku, href
            else:
                yield sku, f"sku:{sku}"

    @classmethod
    def _extract_card_href_for_title_node(cls, driver: webdriver.Chrome, title_node: Any) -> str:
        try:
            href = driver.execute_script(
                """
                const node = arguments[0];
                if (!node) return '';
                const direct = node.closest('a[href], [data-href], [data-url], [data-link], [to]');
                if (direct) {
                  return (
                    direct.getAttribute('href') ||
                    direct.getAttribute('data-href') ||
                    direct.getAttribute('data-url') ||
                    direct.getAttribute('data-link') ||
                    direct.getAttribute('to') ||
                    ''
                  );
                }
                const card = node.closest('[class*="product-card"], [class*="product"], li, article, div');
                if (!card) return '';
                const anchor = card.querySelector('a[href], [data-href], [data-url], [data-link], [to]');
                if (!anchor) return '';
                return (
                  anchor.getAttribute('href') ||
                  anchor.getAttribute('data-href') ||
                  anchor.getAttribute('data-url') ||
                  anchor.getAttribute('data-link') ||
                  anchor.getAttribute('to') ||
                  ''
                );
                """,
                title_node,
            )
        except Exception:
            href = ""

        value = cls._clean_text(href)
        if not value:
            return ""
        if value.startswith("http"):
            return value
        if "/products/" in value or "/product/" in value:
            return urljoin(driver.current_url, value)
        return ""

    @classmethod
    def load_more(cls, driver: webdriver.Chrome) -> bool:
        initial_card_count = len(cls._find_all(driver, cls.PRODUCT_CARD_TITLE_SELECTORS))
        cls._expand_visible_show_more_buttons(driver)
        before_scroll_y, _, _, at_bottom_before = cls._get_scroll_snapshot(driver)

        if at_bottom_before:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        else:
            driver.execute_script("window.scrollBy(0, Math.max(window.innerHeight * 0.85, 600));")
        cls.random_wait()

        button = cls._find_first(driver, cls.LOAD_MORE_SELECTORS, timeout=3, clickable=True)
        clicked_load_more = False
        if button is not None:
            try:
                button.click()
                clicked_load_more = True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", button)
                    clicked_load_more = True
                except Exception:
                    clicked_load_more = False
            if clicked_load_more:
                cls.random_wait()

        for _ in range(40):
            new_card_count = len(cls._find_all(driver, cls.PRODUCT_CARD_TITLE_SELECTORS))
            after_scroll_y, _, _, at_bottom_after = cls._get_scroll_snapshot(driver)
            if new_card_count > initial_card_count:
                return True
            if after_scroll_y > before_scroll_y and not at_bottom_after:
                return True
            if not at_bottom_after:
                return True
            time.sleep(0.4)

        if clicked_load_more:
            return len(cls._find_all(driver, cls.PRODUCT_CARD_TITLE_SELECTORS)) > initial_card_count
        return False

    @classmethod
    def _expand_visible_show_more_buttons(cls, driver: webdriver.Chrome) -> int:
        script = """
            const buttons = Array.from(
              document.querySelectorAll('.product-card__show-more-button, [class*="product-card__show-more-button"]')
            );
            let clicked = 0;
            for (const button of buttons) {
              if (button.dataset.codexExpanded === '1') continue;
              if (!button.offsetParent) continue;
              if (button.disabled || button.getAttribute('aria-disabled') === 'true') continue;
              button.dataset.codexExpanded = '1';
              button.click();
              clicked += 1;
            }
            return clicked;
        """
        try:
            value = driver.execute_script(script)
            return int(value or 0)
        except Exception:
            return 0

    @staticmethod
    def _get_scroll_snapshot(driver: webdriver.Chrome) -> tuple[int, int, int, bool]:
        script = """
            const y = Math.floor(window.scrollY || window.pageYOffset || 0);
            const h = Math.max(
              document.body ? document.body.scrollHeight : 0,
              document.documentElement ? document.documentElement.scrollHeight : 0
            );
            const viewport = Math.floor(window.innerHeight || 0);
            const atBottom = y + viewport >= h - 10;
            return [y, h, viewport, atBottom];
        """
        try:
            y, h, viewport, at_bottom = driver.execute_script(script)
            return int(y), int(h), int(viewport), bool(at_bottom)
        except Exception:
            return 0, 0, 0, True

    @classmethod
    def _find_product_target(cls, driver: webdriver.Chrome, sku: str, timeout: float = 8):
        sku_upper = sku.upper()
        return cls._find_first(
            driver,
            (
                (
                    By.XPATH,
                    f"//a[contains(@href,'/products/') and contains(translate(@href,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku_upper}')]",
                ),
                (
                    By.XPATH,
                    f"//a[contains(@href,'/product/') and contains(translate(@href,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku_upper}')]",
                ),
                (
                    By.XPATH,
                    f"//*[contains(@class,'product-card') and contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku_upper}')]//a[contains(@href,'/products/') or contains(@href,'/product/')]",
                ),
                (
                    By.XPATH,
                    f"//*[contains(@class,'product-card__title') and contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku_upper}')]",
                ),
            ),
            timeout=timeout,
            clickable=True,
        )

    @classmethod
    def _extract_product_href_for_sku(cls, driver: webdriver.Chrome, sku: str) -> str:
        sku_upper = sku.upper()
        anchor = cls._find_first(
            driver,
            (
                (
                    By.XPATH,
                    f"//a[contains(@href,'/products/') and contains(translate(@href,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku_upper}')]",
                ),
                (
                    By.XPATH,
                    f"//a[contains(@href,'/product/') and contains(translate(@href,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku_upper}')]",
                ),
                (
                    By.XPATH,
                    f"//*[contains(@class,'product-card') and contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{sku_upper}')]//a[contains(@href,'/products/') or contains(@href,'/product/')][1]",
                ),
            ),
            timeout=2,
            clickable=False,
        )
        if anchor is not None:
            href = cls._clean_text(anchor.get_attribute("href"))
            if href:
                return href

        source = driver.page_source
        patterns = (
            rf"https?://[^\s\"']*(?:/products/|/product/)[^\s\"']*{re.escape(sku_upper)}[^\s\"']*",
            rf"/(?:products|product)/[^\s\"']*{re.escape(sku_upper)}[^\s\"']*",
        )
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = cls._clean_text(match.group(0))
            if not candidate:
                continue
            if candidate.startswith("http"):
                return candidate
            return urljoin(driver.current_url, candidate)
        return ""

    @classmethod
    def _is_product_details_context(cls, driver: webdriver.Chrome, expected_sku: str | None = None) -> bool:
        try:
            body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text)
        except Exception:
            return False
        body_l = body_text.lower()
        detail_tokens = (
            "product details",
            "additional information",
            "product information",
            "materiaal",
            "material",
            "fit",
            "fabric composition",
        )

        positive_signals = 0
        if any(token in body_l for token in detail_tokens):
            positive_signals += 2

        if cls._find_first(driver, cls.PRODUCT_NAME_SELECTORS, timeout=0) is not None:
            positive_signals += 1

        detail_nodes = driver.find_elements(
            By.CSS_SELECTOR,
            ".extra-fields tr, table tr, dl dt, .a4f-product-uniqueid, [data-sku]",
        )
        if detail_nodes:
            positive_signals += 1

        has_price = cls._find_first(
            driver,
            (
                (By.CSS_SELECTOR, ".product-price .a4f-price"),
                (By.CSS_SELECTOR, "[class*='price']"),
            ),
            timeout=0,
            clickable=False,
        ) is not None
        has_sizes = len(cls._find_all(driver, cls.SIZE_SELECTORS)) > 0
        has_images = len(cls._find_all(driver, cls.IMAGE_SELECTORS)) > 0
        if sum((1 if has_price else 0, 1 if has_sizes else 0, 1 if has_images else 0)) >= 2:
            positive_signals += 1

        context_skus: set[str] = set()
        context_url_sku = cls._extract_sku_from_url(driver.current_url)
        if context_url_sku:
            context_skus.add(context_url_sku)
        for node in detail_nodes[:12]:
            for value in (
                cls._clean_text(getattr(node, "text", "")),
                cls._clean_text(node.get_attribute("content")),
                cls._clean_text(node.get_attribute("data-sku")),
            ):
                sku_value = cls._extract_sku_from_text(value)
                if sku_value:
                    context_skus.add(sku_value)

        sku_hit = False
        if expected_sku:
            sku_upper = expected_sku.upper()
            sku_base = cls._sku_base(sku_upper)
            current_url_u = driver.current_url.upper()
            strong_match = False
            if sku_upper in current_url_u or (sku_base and sku_base in current_url_u):
                strong_match = True
            if any(cls._sku_matches_expected(candidate, sku_upper) for candidate in context_skus):
                strong_match = True

            if strong_match:
                positive_signals += 2
                sku_hit = True

            # Guard against false positives where expected SKU appears only in swatch/related lists.
            if (
                context_skus
                and not any(cls._sku_matches_expected(candidate, sku_upper) for candidate in context_skus)
                and ("/products/" in current_url_u or "/product/" in current_url_u)
            ):
                return False
            if not sku_hit:
                return False

        listing_signals = 0
        if len(cls._find_all(driver, cls.PRODUCT_CARD_TITLE_SELECTORS)) >= 6:
            listing_signals += 1
        if cls._find_first(driver, cls.LOAD_MORE_SELECTORS, timeout=0, clickable=False) is not None:
            listing_signals += 1
        if "show more products" in body_l or "load more" in body_l:
            listing_signals += 1

        if listing_signals >= 2 and positive_signals == 0:
            return False

        if sku_hit and (has_price or has_sizes or has_images):
            return True
        if cls._looks_like_listing_url(driver.current_url) and positive_signals < 2:
            return False

        return positive_signals >= 2

    @staticmethod
    def _looks_like_listing_url(url: str) -> bool:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in query:
            if key.startswith("categoryCodeForLevel") or key == "category":
                return True
        path = parsed.path.lower().rstrip("/")
        return path.endswith("/products/micro_fashion_04")

    @classmethod
    def _open_product_card(cls, driver: webdriver.Chrome, sku: str) -> bool:
        target = cls._find_product_target(driver, sku, timeout=10)
        if target is None:
            return False

        try:
            target.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", target)
            except Exception:
                return False

        for _ in range(20):
            cls.random_wait()
            if cls._is_product_details_context(driver, sku):
                return True
        return False

    @classmethod
    def _open_product_card_via_search(cls, driver: webdriver.Chrome, sku: str) -> bool:
        search_input = cls._find_first(
            driver,
            (
                (By.ID, "productSearch"),
                (By.NAME, "productSearch"),
                (By.CSS_SELECTOR, "input#productSearch"),
                (By.CSS_SELECTOR, "input[name='productSearch']"),
                *cls.SEARCH_INPUT_SELECTORS,
            ),
            timeout=8,
        )
        if search_input is None:
            return False

        try:
            search_input.clear()
        except Exception:
            pass
        search_input.send_keys(sku)
        cls.random_wait(2)

        target = cls._find_product_target(driver, sku, timeout=10)
        if target is None:
            try:
                search_input.send_keys(Keys.ENTER)
            except Exception:
                return False
            cls.random_wait(2)
            target = cls._find_product_target(driver, sku, timeout=10)
        if target is None:
            return False

        try:
            target.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", target)
            except Exception:
                return False

        for _ in range(20):
            cls.random_wait()
            if cls._is_product_details_context(driver, sku):
                return True
        return False

    @classmethod
    def _open_product_for_sku(cls, driver: webdriver.Chrome, sku: str, category_url: str) -> bool:
        if cls._open_product_card(driver, sku):
            return True

        driver.get(category_url)
        cls.random_wait()
        if cls._open_product_card(driver, sku):
            return True

        if cls._open_product_card_via_search(driver, sku):
            return True

        direct_href = cls._extract_product_href_for_sku(driver, sku)
        if not direct_href:
            return False
        try:
            driver.get(direct_href)
            cls.random_wait()
            return cls._is_product_details_context(driver, sku)
        except Exception:
            return False

    @staticmethod
    def get_done_ids() -> set[str]:
        done_ids = set()
        done_path = os.path.join(PRODUCTS_PATH, "all.csv")
        if os.path.exists(done_path):
            with open(done_path, "r", encoding="utf-8") as file:
                for line in file:
                    if line.strip().lower().startswith("sku"):
                        continue
                    done_ids.add(line.strip().split(",")[0].upper())
        return done_ids

    @staticmethod
    def get_category(url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in (
            "categoryCodeForLevel1",
            "categoryCodeForLevel2",
            "categoryCodeForLevel3",
            "category",
        ):
            values = query.get(key)
            if values and values[0].strip():
                return values[0].strip().replace(" ", "_")

        return parsed.path.split("/")[-1].replace("%20", "_")

    @classmethod
    def save_products(cls, products: list[dict[str, Any]], category: str):
        if not products:
            return
        os.makedirs(PRODUCTS_PATH, exist_ok=True)
        category_path = os.path.join(PRODUCTS_PATH, f"{category.lower()}.csv")
        new = pd.DataFrame(products)
        if "sku" not in new.columns:
            return
        updated = cls.update_names(new)

        def consolidate(df: pd.DataFrame) -> pd.DataFrame:
            if df.empty or "sku" not in df.columns:
                return df
            score_frame = df.drop(columns=[c for c in ("wsp", "description") if c in df.columns])
            score = score_frame.notna().sum(axis=1).astype(float)
            if "image_count" in df.columns:
                image_count = pd.to_numeric(df["image_count"], errors="coerce").fillna(0)
                score = score + (image_count > 0).astype(float) * 2
            ranked = df.copy()
            ranked["_row_score"] = score
            ranked["_row_order"] = range(len(ranked))
            # Prefer newer rows first so re-scrapes replace stale values; use quality score as tie-breaker.
            ranked = ranked.sort_values(["_row_order", "_row_score"], ascending=[False, False])
            ranked = ranked.drop_duplicates(subset=["sku"], keep="first")
            ranked = ranked.drop(columns=["_row_score", "_row_order"])
            return ranked.reset_index(drop=True)

        if not os.path.exists(category_path):
            consolidate(updated).to_csv(category_path, index=False)
        else:
            try:
                existing = pd.read_csv(category_path)
                merged = pd.concat([existing, updated], ignore_index=True)
                consolidate(merged).to_csv(category_path, index=False)
            except pd.errors.EmptyDataError:
                consolidate(updated).to_csv(category_path, index=False)

    @staticmethod
    def get_product_name(driver: webdriver.Chrome) -> str:
        element = Profuomo._find_first(driver, ProfuomoScraper.PRODUCT_NAME_SELECTORS, timeout=4)
        if element is not None:
            name = Profuomo._clean_text(element.text)
            if name:
                return name

        body_text = Profuomo._clean_text(driver.find_element(By.TAG_NAME, "body").text)
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        if "Product details" in lines:
            index = lines.index("Product details")
            for candidate in lines[index + 1 : index + 8]:
                if " - " in candidate:
                    return candidate
            for candidate in lines[index + 1 : index + 5]:
                if candidate:
                    return candidate
        for line in lines:
            if " - " in line and len(line) < 100:
                return line

        meta_node = Profuomo._find_first(
            driver,
            (
                (By.CSS_SELECTOR, "meta[property='og:title']"),
                (By.CSS_SELECTOR, "meta[name='title']"),
            ),
            timeout=0,
        )
        if meta_node is not None:
            meta_name = Profuomo._clean_text(meta_node.get_attribute("content"))
            if meta_name:
                return meta_name

        for script_node in driver.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']"):
            raw = Profuomo._clean_text(script_node.get_attribute("innerHTML"))
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            candidates = payload if isinstance(payload, list) else [payload]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                name = Profuomo._clean_text(candidate.get("name"))
                if name:
                    return name

        raise Exception("Could not find product name")

    @staticmethod
    def _normalize_detail_key(key: str) -> str:
        return re.sub(r"\s+", " ", key.replace(":", "").strip().lower())

    @classmethod
    def _sanitize_detail_value(cls, key: str, value: str) -> str:
        cleaned = cls._clean_text(value).strip("|:- ").strip()
        if not cleaned:
            return ""
        lower = cleaned.lower()
        if lower == "cuff" and key != "cuff":
            return ""
        if lower in {
            "size",
            "image",
            "eur",
            "€",
            "unit price",
            "wsp",
            "rsp",
            "show more",
            "size guide",
            "compare to other product",
            "product information",
            "additional information",
            "go to order grid",
        }:
            return ""
        if key == "stocklabel":
            cleaned = re.sub(
                r"^\s*available\s+from\s*:\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
        return cleaned

    @staticmethod
    def _merge_multi_value(existing: str, incoming: str) -> str:
        values: list[str] = []
        for raw in (existing or "").split("|"):
            value = raw.strip()
            if value and value not in values:
                values.append(value)
        for raw in (incoming or "").split("|"):
            value = raw.strip()
            if value and value not in values:
                values.append(value)
        return " | ".join(values)

    @classmethod
    def _set_detail_value(cls, details: dict[str, str], key: str, value: str) -> None:
        if not key or not value:
            return
        if key in {"sustainability"}:
            details[key] = cls._merge_multi_value(details.get(key, ""), value)
            return
        details[key] = value

    @classmethod
    def _looks_like_size_value(cls, value: str | None) -> bool:
        candidate = cls._clean_text(value)
        if not candidate:
            return False
        if cls._normalize_size(candidate):
            return True
        if re.fullmatch(r"\d+(?:[.,]\d+)?", candidate):
            return True
        return re.fullmatch(r"\d{1,3}[A-Z]?", candidate, flags=re.IGNORECASE) is not None

    @classmethod
    def _extract_details_from_lines(cls, text: str) -> dict[str, str]:
        details: dict[str, str] = {}
        lines = [cls._clean_text(line) for line in text.splitlines() if cls._clean_text(line)]
        if not lines:
            return details

        for idx, line in enumerate(lines):
            if ":" in line:
                key_part, value_part = line.split(":", 1)
                key_norm = cls._normalize_detail_key(key_part)
                mapped_key = cls.DETAIL_KEY_ALIASES.get(key_norm)
                value = cls._sanitize_detail_value(mapped_key or "", value_part)
                if mapped_key and value:
                    cls._set_detail_value(details, mapped_key, value)
                    continue

            key_norm = cls._normalize_detail_key(line)
            mapped_key = cls.DETAIL_KEY_ALIASES.get(key_norm)
            if not mapped_key:
                # Handle single-line "Key Value" rows (e.g. "Sleeve Long sleeve")
                lowered = line.lower()
                split_matched = False
                for alias, mapped in sorted(
                    cls.DETAIL_KEY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
                ):
                    if not lowered.startswith(alias + " "):
                        continue
                    remainder = line[len(alias) :].strip(" :-")
                    sanitized = cls._sanitize_detail_value(mapped, remainder)
                    if sanitized:
                        cls._set_detail_value(details, mapped, sanitized)
                        split_matched = True
                    break
                if split_matched:
                    continue
                continue

            for candidate in lines[idx + 1 : idx + 4]:
                candidate_value = cls._clean_text(candidate)
                if not candidate_value:
                    continue
                candidate_key = cls._normalize_detail_key(candidate_value)
                if candidate_key in cls.DETAIL_KEY_ALIASES:
                    break
                sanitized = cls._sanitize_detail_value(mapped_key, candidate_value)
                if sanitized:
                    cls._set_detail_value(details, mapped_key, sanitized)
                    break
        return details

    @classmethod
    def _extract_color_from_lines(
        cls, lines: list[str], target_sku: str | None = None
    ) -> str:
        candidates: list[str] = []
        for line in lines:
            match = re.search(r"\bPP[A-Z0-9]{5,}\s*-\s*(.+)$", line, flags=re.IGNORECASE)
            if not match:
                continue
            line_sku = cls._extract_sku_from_text(line)
            if target_sku and line_sku and not cls._sku_matches_expected(line_sku, target_sku):
                continue
            color = cls._sanitize_detail_value("color", match.group(1))
            if cls._looks_like_size_value(color):
                continue
            if color:
                candidates.append(color)
        if candidates:
            return candidates[0]

        # Fallback for pages where the "SKU - COLOR" line is not reliable:
        # use the first swatch-like line after the RSP line and before product info blocks.
        rsp_index = -1
        for idx, line in enumerate(lines):
            if "RSP" in line.upper():
                rsp_index = idx
                break
        if rsp_index >= 0:
            stop_tokens = ("COMPARE TO OTHER PRODUCT", "PRODUCT INFORMATION", "ADDITIONAL INFORMATION")
            for line in lines[rsp_index + 1 : rsp_index + 15]:
                upper_line = line.upper()
                if any(token in upper_line for token in stop_tokens):
                    break
                candidate = cls._sanitize_detail_value("color", line)
                if not candidate:
                    continue
                if cls._looks_like_size_value(candidate):
                    continue
                return candidate
        return ""

    @classmethod
    def _extract_color_from_description(cls, description: str) -> str:
        text = cls._clean_text(description).lower()
        if not text:
            return ""
        color_candidates = (
            "raw beige",
            "light green",
            "dark green",
            "light brown",
            "dark brown",
            "mid green",
            "mid brown",
            "mid blue",
            "dark pink",
            "beige",
            "blue",
            "navy",
            "green",
            "brown",
            "white",
            "black",
            "grey",
            "gray",
            "pink",
            "clay",
            "red",
            "sand",
        )
        for color in color_candidates:
            if color in text:
                return color.title()
        return ""

    @classmethod
    def _extract_rsp_price(cls, text: str) -> str:
        if not text:
            return ""
        compact_text = text.replace("\n", " ")
        match = re.search(
            r"RSP\s*(?:EUR|€)?\s*(\d{1,4}(?:[.,]\d{2}))",
            compact_text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return match.group(1).replace(",", ".").strip()

    @classmethod
    def _expand_product_details_section(cls, driver: webdriver.Chrome) -> bool:
        selectors: tuple[tuple[str, str], ...] = (
            (
                By.XPATH,
                "//*[self::button or self::a or self::span][contains(translate(normalize-space(.),"
                " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'show more')]",
            ),
            (
                By.XPATH,
                "//*[self::button or self::a or self::span][contains(translate(normalize-space(.),"
                " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'meer')]",
            ),
            (By.CSS_SELECTOR, "[class*='show-more']"),
        )
        expanded = False
        for by, selector in selectors:
            try:
                elements = driver.find_elements(by, selector)
            except Exception:
                continue
            for element in elements:
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    text = cls._clean_text(getattr(element, "text", ""))
                    if text and "show less" in text.lower():
                        continue
                    driver.execute_script("arguments[0].click();", element)
                    expanded = True
                    time.sleep(0.15)
                    break
                except Exception:
                    continue
            if expanded:
                break
        return expanded

    @classmethod
    def get_product_details(cls, driver: webdriver.Chrome) -> dict[str, Any]:
        details: dict[str, Any] = {}
        cls._expand_product_details_section(driver)
        context_sku = cls._extract_sku_from_url(driver.current_url)
        detail_blocks = cls._find_all(
            driver,
            (
                (By.CSS_SELECTOR, ".product-details"),
                (By.CSS_SELECTOR, "[class*='product-details']"),
                (By.CSS_SELECTOR, ".a4f-product-details"),
            ),
        )

        for block in detail_blocks[:3]:
            block_text = cls._clean_text(block.text)
            if not block_text:
                continue
            line_details = cls._extract_details_from_lines(block_text)
            for key, value in line_details.items():
                if value:
                    cls._set_detail_value(details, key, value)

            lines = [cls._clean_text(line) for line in block_text.splitlines() if cls._clean_text(line)]
            if "color" not in details:
                extracted_color = cls._extract_color_from_lines(lines, target_sku=context_sku)
                if extracted_color:
                    details["color"] = extracted_color
            if "rrp" not in details:
                rsp_price = cls._extract_rsp_price(block_text)
                if rsp_price:
                    details["rrp"] = rsp_price

        try:
            dls = driver.find_elements(By.CSS_SELECTOR, "dl")
            for dl in dls:
                dts = dl.find_elements(By.TAG_NAME, "dt")
                dds = dl.find_elements(By.TAG_NAME, "dd")
                for dt, dd in zip(dts, dds):
                    key_norm = cls._normalize_detail_key(dt.text)
                    mapped_key = cls.DETAIL_KEY_ALIASES.get(key_norm)
                    if not mapped_key:
                        continue
                    value = cls._sanitize_detail_value(mapped_key, dd.text)
                    if mapped_key and value and not details.get(mapped_key):
                        cls._set_detail_value(details, mapped_key, value)
        except Exception:
            pass

        try:
            rows = driver.find_elements(By.CSS_SELECTOR, ".extra-fields tr, table tr")
            for row in rows:
                tds = row.find_elements(By.TAG_NAME, "td")
                if len(tds) != 2:
                    continue
                key_norm = cls._normalize_detail_key(tds[0].text)
                mapped_key = cls.DETAIL_KEY_ALIASES.get(key_norm)
                if not mapped_key:
                    continue
                value = cls._sanitize_detail_value(mapped_key, tds[1].text)
                if mapped_key and value and not details.get(mapped_key):
                    cls._set_detail_value(details, mapped_key, value)
        except Exception:
            pass

        # Final fallback from visible body text only (avoid raw source which introduces noisy keys).
        if not details.get("rrp") or not details.get("color"):
            try:
                body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text)
            except Exception:
                body_text = ""
            if body_text:
                body_lines = [cls._clean_text(line) for line in body_text.splitlines() if cls._clean_text(line)]
                if not details.get("color"):
                    body_color = cls._extract_color_from_lines(body_lines, target_sku=context_sku)
                    if body_color:
                        details["color"] = body_color
                if not details.get("rrp"):
                    body_rsp = cls._extract_rsp_price(body_text)
                    if body_rsp:
                        details["rrp"] = body_rsp

        # RSP from dedicated price nodes, if still missing.
        price_text_candidates: list[str] = []
        try:
            price_nodes = driver.find_elements(
                By.CSS_SELECTOR, ".product-price .a4f-price, [class*='price']"
            )
            for node in price_nodes:
                node_text = cls._clean_text(node.text)
                if node_text:
                    price_text_candidates.append(node_text)
        except Exception:
            pass

        if not details.get("rrp"):
            for candidate in price_text_candidates:
                rsp_price = cls._extract_rsp_price(candidate)
                if rsp_price:
                    details["rrp"] = rsp_price
                    break

        color_value = cls._clean_text(str(details.get("color", "")))
        if cls._looks_like_size_value(color_value):
            details["color"] = ""
            color_value = ""
        if not color_value:
            description_color = cls._extract_color_from_description(
                cls._clean_text(str(details.get("description", "")))
            )
            if description_color:
                details["color"] = description_color

        # Keep description internal-only (used as color fallback), not persisted in CSV schema.
        details.pop("description", None)
        # Explicitly do not scrape/store WSP; Magento upload uses only RSP/rrp.
        details.pop("wsp", None)

        return details

    @classmethod
    def get_product_sizes(cls, driver: webdriver.Chrome) -> list[str]:
        sizes: list[str] = []
        seen: set[str] = set()

        for node in cls._find_all(driver, cls.SIZE_SELECTORS):
            raw_size = cls._clean_text(node.get_attribute("data-size")) or cls._clean_text(node.text)
            size = cls._normalize_size(raw_size)
            if not size or size in seen:
                continue
            seen.add(size)
            sizes.append(size)

        return sizes

    @classmethod
    def _is_variant_image_url(cls, url: str) -> bool:
        value = cls._normalize_candidate_image_url(url).lower()
        if not value:
            return True
        if value.endswith(".svg") or ".svg?" in value:
            return True
        if any(token in value for token in ("logo", "icon", "sprite", "loading", "spinner")):
            return True
        if "/resize/200/200/" in value:
            return True
        variant_tokens = (
            "swatch",
            "colour-swatch",
            "color-swatch",
            "related",
            "upsell",
            "cross-sell",
            "lookbook",
            "recommend",
        )
        return any(token in value for token in variant_tokens)

    @classmethod
    def _normalize_download_image_url(cls, url: str, target_sku: str | None = None) -> str:
        normalized = cls._normalize_candidate_image_url(url)
        if not normalized:
            return ""
        lower = normalized.lower()
        if "/resize/200/200/" in lower:
            normalized = re.sub(
                r"/resize/\d+/\d+/",
                "/resize/1536/2048/",
                normalized,
                flags=re.IGNORECASE,
            )
        if target_sku:
            sku_upper = cls._clean_text(target_sku).upper()
            sku_base = cls._sku_base(sku_upper)
            u = normalized.upper()
            if sku_upper and sku_upper in u:
                return normalized
            if sku_base and sku_base in u:
                return normalized
        return normalized

    @classmethod
    def _is_excluded_image_context(cls, driver: webdriver.Chrome, img: Any) -> bool:
        script = """
            const node = arguments[0];
            if (!node) return true;
            const excludedSelectors = [
              "[class*='swatch']",
              "[class*='color-swatch']",
              "[class*='colour-swatch']",
              "[class*='related']",
              "[class*='recommend']",
              "[class*='lookbook']",
              "[class*='upsell']",
              "[class*='cross-sell']",
              "[data-testid*='swatch']",
              "[data-testid*='related']",
              "[data-testid*='recommend']",
            ];
            return excludedSelectors.some((selector) => node.closest(selector));
        """
        try:
            return bool(driver.execute_script(script, img))
        except Exception:
            return False

    @classmethod
    def _collect_main_gallery_candidates(cls, driver: webdriver.Chrome) -> list[str]:
        script = """
            const gallerySelectors = [
              ".a4f-images",
              ".product-gallery",
              "[class*='product-gallery']",
              "[class*='gallery-main']",
              "[data-testid*='gallery']",
              "[class*='pdp-gallery']",
            ];
            const excludedContainerSelectors = [
              "[class*='swatch']",
              "[class*='related']",
              "[class*='recommend']",
              "[class*='lookbook']",
              "[class*='upsell']",
              "[class*='cross-sell']",
            ];
            const out = [];
            const seen = new Set();
            const push = (value) => {
              if (!value || typeof value !== "string") return;
              const v = value.trim();
              if (!v) return;
              if (seen.has(v)) return;
              seen.add(v);
              out.push(v);
            };

            const containers = [];
            for (const sel of gallerySelectors) {
              for (const node of document.querySelectorAll(sel)) containers.push(node);
            }
            const uniqueContainers = Array.from(new Set(containers));
            for (const container of uniqueContainers) {
              if (!container) continue;
              const isExcluded = excludedContainerSelectors.some((selector) =>
                container.closest(selector)
              );
              if (isExcluded) continue;
              const images = container.querySelectorAll("img");
              for (const img of images) {
                const imageExcluded = excludedContainerSelectors.some((selector) =>
                  img.closest(selector)
                );
                if (imageExcluded) continue;
                push(img.getAttribute("src"));
                push(img.getAttribute("data-src"));
                push(img.getAttribute("data-zoom-image"));
                push(img.currentSrc || "");
                const srcset = (img.getAttribute("srcset") || "").trim();
                if (srcset) {
                  for (const part of srcset.split(",")) {
                    push((part.trim().split(" ")[0] || "").trim());
                  }
                }
              }
            }
            return out;
        """
        try:
            values = driver.execute_script(script)
            if isinstance(values, list):
                return [cls._clean_text(v) for v in values if cls._clean_text(v)]
            return []
        except Exception:
            return []

    @classmethod
    def _collect_sku_image_candidates_from_source(
        cls, driver: webdriver.Chrome, target_sku: str | None
    ) -> list[str]:
        sku = cls._clean_text(target_sku).upper()
        if not sku:
            return []
        sku_base = cls._sku_base(sku)

        try:
            source = cls._clean_text(driver.page_source)
        except Exception:
            return []
        if not source:
            return []

        normalized = source.replace("\\/", "/")
        upper_normalized = normalized.upper()
        indices = [m.start() for m in re.finditer(re.escape(sku), upper_normalized)]
        if not indices:
            return []

        urls: list[str] = []
        seen: set[str] = set()
        url_pattern = re.compile(
            r"(https?://[^\"'\s<>]+|//[^\"'\s<>]+)",
            flags=re.IGNORECASE,
        )
        image_hint_tokens = (".jpg", ".jpeg", ".png", ".webp", "/media/", "/images/", "/image/")

        for index in indices[:20]:
            window_start = max(0, index - 5000)
            window_end = min(len(normalized), index + 18000)
            window = normalized[window_start:window_end]
            for match in url_pattern.finditer(window):
                candidate = cls._normalize_candidate_image_url(match.group(1))
                if not candidate:
                    continue
                lower_candidate = candidate.lower()
                if not lower_candidate.startswith("http"):
                    continue
                if not any(token in lower_candidate for token in image_hint_tokens):
                    continue
                upper_candidate = candidate.upper()
                if sku not in upper_candidate and sku_base not in upper_candidate:
                    continue
                if cls._is_variant_image_url(candidate):
                    continue
                if candidate in seen:
                    continue
                seen.add(candidate)
                urls.append(candidate)
                if len(urls) >= 20:
                    return urls
        return urls

    @classmethod
    def _collect_image_urls(
        cls,
        driver: webdriver.Chrome,
        strict_gallery: bool = True,
        target_sku: str | None = None,
    ) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        max_urls = 20

        for raw_url in cls._collect_main_gallery_candidates(driver):
            url = cls._normalize_candidate_image_url(raw_url)
            if not url or not url.startswith("http"):
                continue
            if cls._is_variant_image_url(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= max_urls:
                return urls

        for img in cls._find_all(driver, cls.IMAGE_SELECTORS):
            if strict_gallery and cls._is_excluded_image_context(driver, img):
                continue
            candidate_values = [
                img.get_attribute("src"),
                img.get_attribute("data-src"),
                img.get_attribute("data-zoom-image"),
            ]
            srcset = cls._clean_text(img.get_attribute("srcset"))
            if srcset:
                candidate_values.extend(part.strip().split(" ")[0] for part in srcset.split(","))

            for value in candidate_values:
                url = cls._normalize_candidate_image_url(value)
                if not url or not url.startswith("http"):
                    continue
                if cls._is_variant_image_url(url):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                if len(urls) >= max_urls:
                    return urls

        if not urls:
            try:
                meta = driver.find_element(By.CSS_SELECTOR, "meta[property='og:image']")
                meta_url = cls._normalize_candidate_image_url(meta.get_attribute("content"))
                if meta_url.startswith("http") and not cls._is_variant_image_url(meta_url):
                    urls.append(meta_url)
            except Exception:
                pass

        if target_sku and len(urls) < 3:
            for candidate in cls._collect_sku_image_candidates_from_source(driver, target_sku):
                if candidate in seen:
                    continue
                seen.add(candidate)
                urls.append(candidate)
                if len(urls) >= max_urls:
                    break

        return urls

    @classmethod
    def download_images(
        cls,
        driver: webdriver.Chrome,
        sku: str,
        strict_gallery: bool = True,
        target_sku: str | None = None,
    ) -> int:
        sku_folder = os.path.join(PRODUCTS_PATH, sku)
        os.makedirs(sku_folder, exist_ok=True)
        downloaded_count = 0
        existing_valid_count = 0
        for existing in Path(sku_folder).glob("*"):
            if not existing.is_file():
                continue
            try:
                if existing.stat().st_size >= cls.MIN_IMAGE_BYTES:
                    existing_valid_count += 1
            except OSError:
                continue

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/145.0.0.0 Safari/537.36"
                ),
                "Referer": driver.current_url,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
        )
        try:
            for cookie in driver.get_cookies():
                session.cookies.set(
                    cookie.get("name", ""),
                    cookie.get("value", ""),
                    domain=cookie.get("domain"),
                    path=cookie.get("path", "/"),
                )
        except Exception:
            pass

        image_urls: list[str] = []
        for _ in range(8):
            image_urls = cls._collect_image_urls(
                driver,
                strict_gallery=strict_gallery,
                target_sku=target_sku or sku,
            )
            if image_urls:
                break
            time.sleep(0.2)

        if not image_urls:
            print(f"Warning: no image URLs detected for {sku} (strict_gallery={strict_gallery})")

        sanitized_urls: list[str] = []
        seen_urls: set[str] = set()
        for raw in image_urls:
            candidate = cls._normalize_download_image_url(raw, target_sku=target_sku or sku)
            if not candidate or not candidate.startswith("http"):
                continue
            if cls._is_variant_image_url(candidate):
                continue
            if candidate in seen_urls:
                continue
            seen_urls.add(candidate)
            sanitized_urls.append(candidate)

        if not sanitized_urls and image_urls:
            print(f"Warning: image candidates normalized to zero for {sku}")

        for index, img_url in enumerate(sanitized_urls):
            try:
                response = session.get(img_url, timeout=20)
                if response.status_code != 200:
                    continue
                content_type = cls._clean_text(response.headers.get("Content-Type", "")).lower()
                # Some supplier CDN endpoints return generic content-type for valid image bytes.
                if (
                    content_type
                    and "image" not in content_type
                    and "octet-stream" not in content_type
                ):
                    continue
                img_data = response.content
            except Exception:
                continue

            if not img_data:
                continue

            img_path = os.path.join(sku_folder, f"{sku}_{index}.jpg")
            with open(img_path, "wb") as img_file:
                img_file.write(img_data)

            if os.path.getsize(img_path) < cls.MIN_IMAGE_BYTES:
                os.remove(img_path)
                print(f"Warning: Image too small for {sku}_{index}.jpg, skipping...")
                continue
            downloaded_count += 1

        if downloaded_count > 0:
            return downloaded_count
        if existing_valid_count == 0:
            print(
                f"Warning: no valid downloaded images for {sku} "
                f"(strict_gallery={strict_gallery}, candidates={len(sanitized_urls)})"
            )
        return existing_valid_count

    @classmethod
    def _ensure_product_detail_context(cls, driver: webdriver.Chrome, sku: str) -> None:
        if cls._is_product_details_context(driver, sku):
            return

        if cls._open_product_card_via_search(driver, sku):
            cls.random_wait()
        if cls._is_product_details_context(driver, sku):
            return

        direct_href = cls._extract_product_href_for_sku(driver, sku)
        if direct_href:
            driver.get(direct_href)
            cls.random_wait()
        if cls._is_product_details_context(driver, sku):
            return

        sku_upper = sku.upper()
        page_has_sku = sku_upper in driver.page_source.upper()
        if cls._looks_like_listing_url(driver.current_url) and not page_has_sku:
            raise Exception(f"Product detail context not found for {sku}")
        print(f"Warning: detail context weak for {sku}, continuing with best-effort extraction")

    @classmethod
    def get_product_sku(cls, driver: webdriver.Chrome) -> str:
        body_text = cls._clean_text(driver.find_element(By.TAG_NAME, "body").text)
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        if "Product details" in lines:
            index = lines.index("Product details")
            for candidate in lines[index + 1 : index + 8]:
                sku = cls._extract_sku_from_text(candidate)
                if sku:
                    return sku

        sku_from_url = cls._extract_sku_from_url(driver.current_url)
        if sku_from_url:
            return sku_from_url

        selectors: tuple[tuple[str, str], ...] = (
            (By.CLASS_NAME, "a4f-product-uniqueid"),
            (By.CSS_SELECTOR, "[class*='product-details'] [class*='product'][class*='id']"),
            (By.CSS_SELECTOR, "[class*='product-details'] [data-sku]"),
            (By.CSS_SELECTOR, "meta[itemprop='sku']"),
        )

        for node in cls._find_all(driver, selectors):
            values = [node.text, node.get_attribute("content"), node.get_attribute("data-sku")]
            for value in values:
                sku = cls._extract_sku_from_text(value)
                if sku:
                    return sku

        source = driver.page_source
        match = re.search(r'"sku"\s*:\s*"(?P<sku>PP[A-Z0-9]{5,})"', source, flags=re.IGNORECASE)
        if match:
            return match.group("sku").upper()

        text_match = cls.SKU_REGEX.search(source)
        if text_match:
            return text_match.group(1).upper()

        raise Exception("Could not extract SKU from product page")

    @classmethod
    def scrape_product(
        cls,
        driver: webdriver.Chrome,
        url: str,
        category: str,
        forced_sku: str | None = None,
    ) -> dict[str, Any]:
        driver.get(url)
        cls.random_wait()

        if forced_sku:
            cls._ensure_product_detail_context(driver, forced_sku)

        product: dict[str, Any] = {
            "category": category,
            "sizes": [],
            "color": "",
            "capsule": "",
            "collar": "",
            "sleeve": "",
            "cuff": "",
            "sustainability": "",
            "noniron": "",
            "quality": "",
            "rrp": "",
        }

        if forced_sku:
            product["sku"] = forced_sku.upper()
        else:
            try:
                product["sku"] = cls.get_product_sku(driver)
            except Exception as e:
                raise Exception(f"Failed to get SKU: {e}")

        try:
            product["name"] = cls.get_product_name(driver)
        except Exception:
            product["name"] = ""

        try:
            product.update(cls.get_product_details(driver))
            product["sizes"] = cls.get_product_sizes(driver)
        except Exception as e:
            print(f"Warning: Some product details failed for {product.get('sku', 'Unknown')}: {e}")

        image_count = 0
        try:
            image_count = cls.download_images(
                driver,
                product["sku"],
                strict_gallery=True,
                target_sku=forced_sku or product["sku"],
            )
            if image_count == 0:
                print(
                    f"Warning: no valid strict-gallery images for {product['sku']}; "
                    "retrying after reload"
                )
                try:
                    driver.get(url)
                    cls.random_wait()
                    if forced_sku:
                        cls._ensure_product_detail_context(driver, forced_sku)
                except Exception as reload_error:
                    print(
                        f"Warning: reload/context refresh failed for {product['sku']}: {reload_error}"
                    )
                image_count = cls.download_images(
                    driver,
                    product["sku"],
                    strict_gallery=True,
                    target_sku=forced_sku or product["sku"],
                )
            if image_count == 0:
                print(
                    f"Warning: strict-gallery image detection failed for {product['sku']}; "
                    "trying relaxed gallery detection"
                )
                image_count = cls.download_images(
                    driver,
                    product["sku"],
                    strict_gallery=False,
                    target_sku=forced_sku or product["sku"],
                )
        except Exception as e:
            print(f"Warning: Image download failed for {product.get('sku', 'Unknown')}: {e}")
        product["image_count"] = image_count
        product["has_images"] = image_count > 0

        return product

    @classmethod
    def scrape_profuomo(cls, url: str):
        data = {"message": "", "error": ""}
        options = webdriver.ChromeOptions()
        driver = cls._create_chrome_driver(options)
        try:
            # Use explicit waits in helper methods; implicit waits make each selector
            # query block for too long on the new dynamic category/product pages.
            driver.implicitly_wait(0)
            driver.maximize_window()

            required_links: list[str] = []
            required_link_set: set[str] = set()
            category = cls.get_category(url)
            failed_count = 0
            skipped_no_images_count = 0

            cls.profuomo_login(driver)
            driver.get(url)
            cls.random_wait()

            listing_round = 0
            no_new_link_rounds = 0
            while listing_round < 400:
                listing_round += 1
                links_before_round = len(required_links)
                for _, link in cls.get_all_products(driver):
                    if link in required_link_set:
                        continue
                    required_link_set.add(link)
                    required_links.append(link)
                if len(required_links) == links_before_round:
                    no_new_link_rounds += 1
                else:
                    no_new_link_rounds = 0
                if not cls.load_more(driver):
                    break
                if no_new_link_rounds >= 6:
                    print("Warning: Listing traversal stopped after 6 rounds with no new products")
                    break
            else:
                print("Warning: Listing traversal hit safety cap (400 rounds)")

            products: list[dict[str, Any]] = []
            for i, link in enumerate(required_links):
                try:
                    try:
                        driver.current_url
                    except Exception:
                        print("Browser driver has crashed or is no longer responsive")
                        break

                    print(f"Scraping product {i + 1}/{len(required_links)}: {link}")
                    if link.startswith("sku:"):
                        sku = link.split(":", 1)[1].strip().upper()
                        if not cls._open_product_for_sku(driver, sku, url):
                            raise Exception(f"Could not open product card for {sku}")
                        product_url = driver.current_url
                        if "/products/" not in product_url and "/product/" not in product_url:
                            raise Exception(f"Product page not opened for {sku}")
                        product = cls.scrape_product(
                            driver,
                            product_url,
                            category,
                            forced_sku=sku,
                        )
                    else:
                        product = cls.scrape_product(driver, link, category)

                    if not product.get("has_images", False):
                        msg = (
                            f"Skipped {product.get('sku', 'Unknown SKU')}: "
                            "no valid supplier images found"
                        )
                        print(f"Warning: {msg}")
                        try:
                            debug_dir = Path("profuomo_debug")
                            debug_dir.mkdir(parents=True, exist_ok=True)
                            timestamp = int(time.time())
                            sku_label = re.sub(
                                r"[^A-Za-z0-9_-]+",
                                "_",
                                product.get("sku", "UNKNOWN"),
                            )
                            debug_html = debug_dir / f"{timestamp}_no_images_{sku_label}.html"
                            debug_png = debug_dir / f"{timestamp}_no_images_{sku_label}.png"
                            debug_html.write_text(driver.page_source, encoding="utf-8")
                            driver.save_screenshot(str(debug_png))
                        except Exception:
                            pass
                        cls._capture_debug_artifacts(
                            driver, f"scrape_no_images_{product.get('sku', 'UNKNOWN')}"
                        )
                        skipped_no_images_count += 1
                        with open("scraping_errors.log", "a", encoding="utf-8") as f:
                            f.write(msg + "\n")
                        continue
                    products.append(product)
                    print(f"Successfully scraped: {product.get('sku', 'Unknown SKU')}")
                except Exception as e:
                    failed_count += 1
                    print(f"Failed to scrape {link}: {str(e)}")
                    with open("scraping_errors.log", "a", encoding="utf-8") as f:
                        f.write(f"Failed to scrape {link}: {str(e)}\n")
                    continue

            cls.save_products(products, category)
            cls.save_products(products, "all")
            data["message"] = (
                f"Finished ({len(products)} saved / {len(required_links)} found, "
                f"{failed_count} failed, {skipped_no_images_count} skipped: no images)"
            )
        except Exception as e:
            print(f"Critical error in scrape_profuomo: {str(e)}")
            data["message"] = ""
            data["error"] = str(e)
            with open("scraping_errors.log", "a", encoding="utf-8") as f:
                f.write(f"Critical error in scrape_profuomo: {str(e)}\n")
        finally:
            driver.quit()
            return data

    @classmethod
    def update_names(cls, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "sku" not in df.columns:
            return df
        if "Productnaam" not in df.columns:
            df["Productnaam"] = ""

        done = set()
        for row in cls.gen_names_df():
            df.loc[df["sku"] == row["sku"], "Productnaam"] = row["Productnaam"]
            done.add(row["sku"])

        for _, row in df[~df["sku"].isin(done)].iterrows():
            naam = row.get("Productnaam")
            if (
                not naam
                or str(naam) == "nan"
                or str(naam) == "NaN"
                or pd.isna(naam)
                or str(naam).endswith(" nan")
            ):
                df.loc[df["sku"] == row["sku"], "Productnaam"] = cls.create_name(row)
        return df

    @classmethod
    def create_name(cls, row: pd.Series) -> str:
        color = row.get("color", "")
        collar = row.get("collar", "")
        category = row.get("category", "")

        if str(category).lower() == "overshirts" and (
            not collar or collar == "" or str(collar).lower() == "nan"
        ):
            collar = "overshirt"

        if collar and collar != "" and str(collar).lower() != "nan":
            return f"Profuomo {color} {collar}".capitalize()
        return f"Profuomo {color}".capitalize()

    @classmethod
    def translate_to_dutch_with_openai(cls, text: str) -> str:
        response = cls.template_service.translate_to_dutch(text)
        return response or ""

    @classmethod
    def gen_names_df(cls):
        for extracted_part in cls.extract_names_and_skus():
            *name, sku = extracted_part.split("-")
            name_text = " ".join(name)
            if not name_text.startswith("Profuomo"):
                name_text = f"Profuomo {name_text}"
            yield {"Productnaam": name_text, "sku": sku.upper()}

    @classmethod
    @cache
    def extract_names_and_skus(cls) -> list[str]:
        try:
            response = requests.get(cls.name_update_url, timeout=5)
            return re.findall(
                r"https://profuomo\.com/nl/([^/]+)\.html",
                response.content.decode("utf-8"),
            )
        except Exception as e:
            print(f"Warning: Could not access sitemap for name updates: {e}")
            return []
