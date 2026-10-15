"""Loads the sample documents from sample_data/ into MongoDB.
Reads MONGO_URI and MONGO_DB from the environment or .env file.
"""

import os
import pathlib

import certifi
from bson import json_util
from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables from .env
load_dotenv(override=True)

# Locate the sample JSON files
here = pathlib.Path(__file__).parent / "sample_data"

# Preserve timezone-aware dates while reading sample documents
opts = json_util.JSONOptions(tz_aware=True)

# Read MongoDB connection settings
mongo_uri = os.getenv("MONGO_URI")
mongo_db = os.getenv("MONGO_DB", "attendance_db")

if not mongo_uri:
    raise ValueError(
        "MONGO_URI is missing. Please check your .env file."
    )

# Connect securely to MongoDB Atlas using certifi's CA certificates
client = MongoClient(
    mongo_uri,
    tlsCAFile=certifi.where(),
    tz_aware=True,
    serverSelectionTimeoutMS=15000,
)

try:
    # Verify the connection before modifying any collections
    client.admin.command("ping")
    print("MongoDB connection successful.")

    db = client[mongo_db]

    # Load the sample collections
    for name in ("employees", "attendance_logs"):
        file_path = here / f"{name}.json"

        if not file_path.exists():
            raise FileNotFoundError(
                f"Sample data file not found: {file_path}"
            )

        docs = json_util.loads(
            file_path.read_text(encoding="utf-8"),
            json_options=opts,
        )

        if not isinstance(docs, list):
            raise ValueError(
                f"{file_path} must contain a JSON array."
            )

        # WARNING: This replaces existing documents in these collections.
        db[name].delete_many({})

        if docs:
            db[name].insert_many(docs)

        print(
            f"Loaded {len(docs):>2} documents into "
            f"'{db.name}.{name}'."
        )

    print("Sample data loading completed successfully.")

finally:
    client.close()
