from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from pymongo import MongoClient
from flask_apscheduler import APScheduler


db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()

# MongoDB Initialization
MONGO_URI = "mongodb://localhost:27017"
mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["collective_rcm"]
scheduler      = APScheduler()
