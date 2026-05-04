import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("PZUl1wN43Qgali3zYy")
API_SECRET = os.getenv("JpO55xnttEfw7Ic20RJwrJeskMjst4SbxJ4u")

if not API_KEY or not API_SECRET:
    raise ValueError("BYBIT_API_KEY and BYBIT_API_SECRET must be set in .env")