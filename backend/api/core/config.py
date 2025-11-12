import os
from dotenv import load_dotenv

# Load .env from project root
load_dotenv()

NBA_API_BASE_URL = os.getenv("NBA_API_BASE_URL", "https://api.balldontlie.io/v1/games")
NBA_API_KEY = os.getenv("NBA_API_KEY", "").strip()
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Chicago")

# Optional: sanity log (doesn't print the key)
print(f"[CFG] env loaded. key_present={bool(NBA_API_KEY)} url={NBA_API_BASE_URL}")
