import os
import sys
from pathlib import Path
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(".env")

# Fix path resolution for cross-platform compatibility
if getattr(sys, "frozen", False):
    # Running as PyInstaller executable
    BASE_DIR = Path(sys.executable).parent
else:
    # Running as Python script
    BASE_DIR = Path(__file__).parent

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
