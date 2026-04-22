import os
import sys
from pathlib import Path
from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
PASSWORD = os.getenv("EMAIL_PASSWORD", "")
MAGENTO_USERNAME = os.getenv("MAGENTO_USERNAME", "")
MAGENTO_PASSWORD = os.getenv("MAGENTO_PASSWORD", "")
PROFUOMO_USERNAME = os.getenv("PROFUOMO_USERNAME", "")
PROFUOMO_PASSWORD = os.getenv("PROFUOMO_PASSWORD", "")
PRODUCTS_PATH = str(BASE_DIR / "products")
MAGENTO_TEST_USERNAME = os.getenv("MAGENTO_TEST_USERNAME", "")
MAGENTO_TEST_PASSWORD = os.getenv("MAGENTO_TEST_PASSWORD", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TEST = os.getenv("TEST", "true").lower() == "true"
OPENAI_MODEL = "gpt-4o"
