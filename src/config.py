import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

MARKIFY_API_KEY = os.getenv("MARKIFY_API_KEY")
MARKIFY_BASE_URL = "https://www.markify.com/api"
RATE_LIMIT_SECONDS = 2.0
HTTP_TIMEOUT = 18

DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
INPUT_XLSX = DATA_DIR / "Markify tests.xlsx"

OUTPUTS_DIR.mkdir(exist_ok=True)

if not MARKIFY_API_KEY:
    raise RuntimeError(
        "MARKIFY_API_KEY is not set. Please set it in the .env file."
    )
