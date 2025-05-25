from pymongo import MongoClient, errors
from gridfs import GridFS
from bson.objectid import ObjectId
import logging
from datetime import datetime
from typing import Optional, Union, List, Tuple
from pymongo.database import Database
import os
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()
# -------------------------------
# Logging Setup
# -------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -------------------------------
# MongoDB Configuration
# -------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGO_DB_NAME", "collective_rcm")
FILES_COLLECTION = "file_metadata"
PROFILE_PICTURES_COLLECTION = "profile_pictures"

# -------------------------------
# Singleton MongoDB Connection
# -------------------------------
global_client = None
global_db = None
global_grid_fs = None

def get_mongo_connection() -> Tuple[MongoClient, Database, GridFS]:
    global global_client, global_db, global_grid_fs
    if not global_client:
        try:
            global_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            global_db = global_client[DB_NAME]
            global_grid_fs = GridFS(global_db)
            logging.info(f"Connected to MongoDB database '{DB_NAME}'.")
        except errors.ServerSelectionTimeoutError as e:
            logging.critical(f"Failed to connect to MongoDB: {e}")
            raise SystemExit(f"Critical error: {e}")
    return global_client, global_db, global_grid_fs

# -------------------------------
# Initialize MongoDB
# -------------------------------
def initialize_mongodb(uri: str = MONGO_URI, db_name: str = DB_NAME):
    """
    Initialize MongoDB connection and return client and database instance.
    """
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.server_info()  # Test connection to MongoDB server
        database = client.get_database(db_name)
        if database is None:
            raise ValueError(f"Failed to retrieve database object for '{db_name}'.")
        logging.info(f"MongoDB initialized and connected to database: {db_name}")
        return client, database
    except errors.ServerSelectionTimeoutError as e:
        logging.critical(f"Failed to connect to MongoDB: {e}")
        raise SystemExit(f"Critical error: {e}")
    except Exception as e:
        logging.critical(f"Unexpected error during MongoDB initialization: {e}")
        raise SystemExit(f"Critical error: {e}")
# -------------------------------
# File Management for Study Materials
# -------------------------------
def save_file_to_gridfs(file_name: str, file_data: bytes, metadata: Optional[dict] = None) -> Optional[str]:
    try:
        _, _, grid_fs = get_mongo_connection()
        file_id = grid_fs.put(file_data, filename=file_name, metadata=metadata)
        logging.info(f"File '{file_name}' saved to GridFS with ID: {file_id}")
        return str(file_id)
    except errors.PyMongoError as e:
        logging.error(f"Error saving file to GridFS: {e}")
        return None

def retrieve_file_from_gridfs(file_id: str) -> Optional[dict]:
    try:
        _, _, grid_fs = get_mongo_connection()
        grid_file = grid_fs.get(ObjectId(file_id))
        return {
            "filename": grid_file.filename,
            "metadata": grid_file.metadata,
            "data": grid_file.read()
        }
    except errors.PyMongoError as e:
        logging.error(f"Error retrieving file with ID {file_id}: {e}")
        return None

def delete_file_from_gridfs(file_id: str) -> dict:
    try:
        _, _, grid_fs = get_mongo_connection()
        grid_fs.delete(ObjectId(file_id))
        return {"status": "deleted", "message": f"File {file_id} deleted from GridFS."}
    except errors.PyMongoError as e:
        return {"status": "error", "message": str(e)}
    
# -------------------------------
# Subtopic and Metadata Management
# -------------------------------
def save_subtopic_metadata(study_material_id: int, title: str, file_id: str, file_type: str) -> str:
    try:
        _, db, _ = get_mongo_connection()
        metadata = {
            "study_material_id": study_material_id,
            "title": title,
            "file_id": file_id,
            "file_type": file_type,
            "created_at": datetime.utcnow()
        }
        result = db[FILES_COLLECTION].insert_one(metadata)
        logging.info(f"Saved subtopic metadata for '{title}' (file_id: {file_id})")
        return str(result.inserted_id)
    except errors.PyMongoError as e:
        logging.error(f"Error saving subtopic metadata: {e}")
        return ""

def retrieve_subtopics_for_material(study_material_id: int) -> List[dict]:
    try:
        _, db, _ = get_mongo_connection()
        return list(db[FILES_COLLECTION].find({"study_material_id": study_material_id}))
    except errors.PyMongoError as e:
        logging.error(f"Error retrieving subtopics: {e}")
        return []

def delete_subtopic_metadata(file_id: str) -> dict:
    try:
        _, db, _ = get_mongo_connection()
        result = db[FILES_COLLECTION].delete_one({"file_id": file_id})
        if result.deleted_count:
            return {"status": "deleted", "message": f"Metadata for file_id '{file_id}' deleted."}
        return {"status": "not_found", "message": "No metadata found to delete."}
    except errors.PyMongoError as e:
        return {"status": "error", "message": str(e)}

# -------------------------------
# Integration with Study Materials
# -------------------------------
def sync_study_material_to_mongo(material: dict, files: List[dict]) -> bool:
    """
    Sync study material data and associated files to MongoDB.

    Args:
        material (dict): The study material metadata.
        files (list): List of file data (each file includes name, data, and type).

    Returns:
        bool: True if sync is successful, False otherwise.
    """
    try:
        for file in files:
            file_id = save_file_to_gridfs(file['name'], file['data'], file.get('metadata'))
            if not file_id:
                logging.error(f"Failed to save file {file.get('name')}")
                continue  # or return False if this is critical
            save_subtopic_metadata(
                study_material_id=material['id'],
                title=file.get('title', 'Untitled'),
                file_id=file_id,
                file_type=file.get('type', 'unknown')
            )
        logging.info(f"Study material {material['id']} synced successfully with MongoDB.")
        return True
    except Exception as e:
        logging.error(f"Error syncing study material {material['id']} to MongoDB: {e}")
        return False

def setup_collections(database: Database):
    """
    Setup MongoDB collections and indexes.

    Args:
        database (Database): MongoDB Database instance.
    """
    try:
        # Validate the database instance
        if not isinstance(database, Database):
            raise ValueError("A valid MongoDB Database instance is required for setup_collections.")

        # Profile Pictures Collection
        profile_pictures_collection = database[PROFILE_PICTURES_COLLECTION]
        profile_pictures_collection.create_index("user_id", unique=True)
        logging.info("Index on 'user_id' created for collection 'profile_pictures'.")

    except Exception as e:
        logging.error(f"Error setting up MongoDB collections: {e}", exc_info=True)
        raise

# -------------------------------
# Profile Picture Management
# -------------------------------
def save_profile_picture(user_id: Union[int, str], image_data: bytes) -> dict:
    try:
        _, db, _ = get_mongo_connection()
        result = db[PROFILE_PICTURES_COLLECTION].update_one(
            {"user_id": str(user_id)},
            {"$set": {"image_data": image_data, "updated_at": datetime.utcnow()}},
            upsert=True
        )
        if result.upserted_id:
            return {"status": "inserted", "id": str(result.upserted_id)}
        return {"status": "updated", "message": "Profile picture updated."}
    except errors.PyMongoError as e:
        logging.error(f"Profile picture save error: {e}")
        return {"status": "error", "message": str(e)}

def get_profile_picture(user_id: Union[int, str]) -> Optional[bytes]:
    try:
        _, db, _ = get_mongo_connection()
        record = db[PROFILE_PICTURES_COLLECTION].find_one({"user_id": str(user_id)})
        return record.get("image_data") if record else None
    except errors.PyMongoError as e:
        logging.error(f"Error retrieving profile picture: {e}")
        return None

def delete_profile_picture(user_id: Union[int, str]) -> dict:
    try:
        _, db, _ = get_mongo_connection()
        result = db[PROFILE_PICTURES_COLLECTION].delete_one({"user_id": str(user_id)})
        if result.deleted_count:
            return {"status": "deleted", "message": f"Deleted profile picture for user {user_id}."}
        return {"status": "not_found", "message": "No profile picture found to delete."}
    except errors.PyMongoError as e:
        logging.error(f"Error deleting profile picture: {e}")
        return {"status": "error", "message": str(e)}