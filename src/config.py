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
KNOWN_BAD_CODES_FILE = DATA_DIR / "known_bad_db_codes.json"

OUTPUTS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
# 注：MARKIFY_API_KEY 是否存在的检查移到 MarkifyClient.from_env()，
# 这样仅 import config（如查路径常量）不会因为缺 key 而崩溃。
