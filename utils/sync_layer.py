from pymongo import MongoClient
from bson.objectid import ObjectId
from gridfs import GridFS
import logging

# MongoDB Initialization
mongo_client = MongoClient('mongodb://localhost:27017/')  # Replace with your MongoDB URI
mongo_db = mongo_client['collective_rcm']  # MongoDB database name
grid_fs = GridFS(mongo_db)

def sync_study_material_to_mongo(material):
    """
    Sync study material data to MongoDB.

    Args:
        material (StudyMaterial): The SQLAlchemy StudyMaterial model instance to sync.

    Returns:
        bool: True if sync is successful, False otherwise.
    """
    if not material:
        logging.error("Error: StudyMaterial object is None. Cannot sync to MongoDB.")
        return False

    try:
        # Prepare metadata for MongoDB
        mongo_data = {
            "material_id": material.id,  # PostgreSQL ID for cross-reference
            "title": material.title,
            "description": material.description,
            "course_time": material.course_time,
            "max_time": material.max_time,
            "created_at": material.created_at.isoformat() if material.created_at else None,
            "file_ids": [],  # To store references to file chunks in MongoDB
        }

        # Handle file uploads stored in PostgreSQL references
        for file_entry in material.files:  # `files` is a text[] column
            try:
                # Parse file_entry (format: "file_id|filename")
                file_id, filename = file_entry.split('|')
                grid_file = grid_fs.find_one({"_id": ObjectId(file_id)})

                if not grid_file:
                    logging.warning(f"File with ID {file_id} not found in GridFS for material {material.id}. Skipping.")
                    continue

                # Append the file details to the MongoDB metadata
                mongo_data["file_ids"].append({
                    "file_id": str(grid_file._id),
                    "filename": grid_file.filename,
                    "content_type": grid_file.content_type or "unknown",
                    "length": grid_file.length,
                })

                logging.info(f"File {filename} (ID: {file_id}) added to MongoDB metadata for material {material.id}.")

            except Exception as e:
                logging.error(f"Error processing file entry {file_entry} for material {material.id}: {e}")
                continue

        # Insert or update the study material in MongoDB
        result = mongo_db.study_materials.update_one(
            {"material_id": material.id},  # Find by PostgreSQL material ID
            {"$set": mongo_data},  # Update metadata
            upsert=True  # Insert if not already present
        )
        logging.info(f"Study material {material.id} successfully synced to MongoDB. Result: {result.raw_result}")
        return True

    except Exception as e:
        logging.error(f"Error syncing study material {material.id} to MongoDB: {e}")
        return False
