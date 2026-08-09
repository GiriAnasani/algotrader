from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Kite Connect Credentials
KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")