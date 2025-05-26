import os
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from pymongo import MongoClient
from flask_apscheduler import APScheduler

# Load .env if running locally
load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()
scheduler = APScheduler()

# MongoDB Initialization from environment
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "collective_rcm")

if not (MONGO_URI.startswith("mongodb://") or MONGO_URI.startswith("mongodb+srv://")):
    print(f"Error: Invalid MONGO_URI: '{MONGO_URI}'. Falling back to default URI: mongodb://localhost:27017/{MONGO_DB_NAME}")
    MONGO_URI = f"mongodb://localhost:27017/{MONGO_DB_NAME}"

mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client[MONGO_DB_NAME]
