import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# Build the connection string from .env variables
connection_string = (
    f"{os.getenv('DB_METHOD')}://{os.getenv('DB_USERNAME')}:"
    f"{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/"
    "?retryWrites=true&w=majority"
)

# Create a global MongoDB client
client = MongoClient(connection_string)

database_name = os.getenv("DB_NAME", "tfvn_bot")
db = client[database_name]
