from flask import Blueprint, request, jsonify, render_template, url_for, make_response, session, redirect, flash
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from gridfs import GridFS
import logging
from bson.objectid import ObjectId
from models import db, StudyMaterial, SubTopic, UserProgress, User, Level, Area, UserLevelProgress, Designation, Category
from datetime import datetime
from io import BytesIO
import PyPDF2
from docx import Document
from pptx import Presentation
from PIL import Image, ImageDraw
from io import BytesIO
from bson.objectid import ObjectId

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize Blueprint
study_material_routes = Blueprint('study_material_routes', __name__)

# MongoDB Client and GridFS Initialization
mongo_client = MongoClient('mongodb://localhost:27017/')  # Replace with your MongoDB connection string
mongo_db = mongo_client['collective_rcm']  # MongoDB database name
grid_fs = GridFS(mongo_db)

# Allowed file extensions
ALLOWED_EXTENSIONS = {'pptx', 'pdf', 'docx', 'txt'}
MAX_FILE_SIZE_MB = 100


def allowed_file(filename):
    """Check if a file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_file_size(file, max_size_mb):
    """Validate if a file's size is within the specified limit."""
    file.seek(0, 2)  # move to end
    size = file.tell()
    file.seek(0)     # reset pointer
    return size <= max_size_mb * 1024 * 1024

def calculate_total_pages(file_like, filetype):
    """
    Calculate total pages/slides for a given file-like object based on its type.
    file_like should be a BytesIO or similar that we can seek(0).
    """
    try:
        file_like.seek(0)
        if filetype == 'pdf':
            reader = PyPDF2.PdfReader(file_like)
            return len(reader.pages)
        elif filetype == 'docx':
            doc = Document(file_like)
            word_count = sum(len(p.text.split()) for p in doc.paragraphs)
            # approximate pages by word count / 300
            return max(1, word_count // 300)
        elif filetype == 'pptx':
            presentation = Presentation(file_like)
            return len(presentation.slides)
        else:
            return 0  # unsupported
    except Exception as e:
        logging.error(f"Error calculating total pages for {filetype}: {e}")
        return 0

@study_material_routes.route('/upload_course', methods=['POST', 'GET'])
def upload_course():
    """
    Handle uploading of study materials and subtopics, with metadata stored in PostgreSQL
    and files stored in MongoDB (GridFS).
    """
    try:
        # Optional: check if user is admin or super_admin
        if not session.get('is_super_admin', False):
            user_role = session.get('role')
            user_designation = session.get('designation_id')
            if user_role != 'admin' and user_designation not in [12]:
                flash("You do not have permission to upload study materials.", "error")
                return redirect(url_for('study_material_routes.list_study_materials'))

        if request.method == 'GET':
            return render_template('upload_study.html')

        # -------------------------
        # 1) Get Form Fields
        # -------------------------
        title = request.form.get('title')
        description = request.form.get('description')
        course_time = request.form.get('course_time')
        max_time = request.form.get('max_time')

        # These are the extra fields you had in the form
        level_id = request.form.get('level_id')
        category_id = request.form.get('category_id')
        minimum_level = request.form.get('minimum_level')

        subtopic_titles = request.form.getlist('subtopic_titles')
        subtopic_files = request.files.getlist('subtopic_files')

        if not title or not description or not course_time or not max_time:
            flash("All fields are required.", "error")
            return redirect(url_for('study_material_routes.upload_course'))

        try:
            course_time = int(course_time)
            max_time = int(max_time)
        except ValueError:
            flash("Course time and max time must be integers.", "error")
            return redirect(url_for('study_material_routes.upload_course'))

        # Convert optional fields to int if possible
        try:
            level_id = int(level_id) if level_id else None
        except:
            level_id = None
        try:
            category_id = int(category_id) if category_id else None
        except:
            category_id = None
        try:
            minimum_level = int(minimum_level) if minimum_level else None
        except:
            minimum_level = None

        # -------------------------
        # 2) Create StudyMaterial in PostgreSQL
        # -------------------------
        study_material = StudyMaterial(
            title=title,
            description=description,
            course_time=course_time,
            max_time=max_time,
            total_pages=0,
            files=[],  # will fill this later
            level_id=level_id,
            category_id=category_id,
            minimum_level=minimum_level
        )
        db.session.add(study_material)
        db.session.commit()
        logging.info(f"Created study material with ID: {study_material.id}")

        # -------------------------
        # 3) Main Documents
        # -------------------------
        files = request.files.getlist('main_documents')  # must match <input name="main_documents">
        file_ids = []
        total_pages = 0

        for file in files:
            if file and allowed_file(file.filename):
                if not validate_file_size(file, MAX_FILE_SIZE_MB):
                    flash(f"{file.filename} exceeds the {MAX_FILE_SIZE_MB}MB limit.", "error")
                    continue

                file_data = file.read()
                # Store in MongoDB (GridFS)
                mongo_id = grid_fs.put(file_data, filename=secure_filename(file.filename))
                # Keep a reference in PostgreSQL
                file_ids.append(f"{mongo_id}|{file.filename}")

                # Use BytesIO for page count
                file_like = BytesIO(file_data)
                extension = file.filename.rsplit('.', 1)[1].lower()
                total_pages += calculate_total_pages(file_like, extension)

        # Update the files list and total_pages
        study_material.files = file_ids
        study_material.total_pages = total_pages
        db.session.commit()
        logging.info(f"Main documents uploaded for study material ID: {study_material.id}")

        # -------------------------
        # 4) Subtopics
        # -------------------------
        for idx, subtopic_title in enumerate(subtopic_titles):
            if not subtopic_title:
                # Skip if the subtopic title is empty
                continue

            subtopic_file = subtopic_files[idx] if idx < len(subtopic_files) else None
            subtopic_file_id = None
            subtopic_pages = 0

            if subtopic_file and allowed_file(subtopic_file.filename):
                if not validate_file_size(subtopic_file, MAX_FILE_SIZE_MB):
                    flash(f"{subtopic_file.filename} exceeds size limit.", "error")
                    continue

                subtopic_file_data = subtopic_file.read()
                # Store in Mongo
                mongo_id = grid_fs.put(subtopic_file_data, filename=secure_filename(subtopic_file.filename))
                subtopic_file_id = str(mongo_id)

                # Calculate pages
                file_like = BytesIO(subtopic_file_data)
                extension = subtopic_file.filename.rsplit('.', 1)[1].lower()
                subtopic_pages = calculate_total_pages(file_like, extension)

            # Create subtopic row in PostgreSQL
            subtopic = SubTopic(
                title=subtopic_title,
                study_material_id=study_material.id,
                file_id=subtopic_file_id,
                page_count=subtopic_pages
            )
            db.session.add(subtopic)

            # Add subtopic pages to the total
            study_material.total_pages += subtopic_pages

        db.session.commit()
        logging.info(f"Subtopics uploaded for study material ID: {study_material.id}")

        flash("Study materials and subtopics uploaded successfully.", "success")
        return redirect(url_for('study_material_routes.list_study_materials'))

    except Exception as e:
        logging.error(f"Error in upload_course: {e}", exc_info=True)
        db.session.rollback()
        flash("An error occurred while uploading the course.", "error")
        return redirect(url_for('study_material_routes.upload_course'))
    
@study_material_routes.route('/start_course/<int:course_id>', methods=['POST'])
def start_course(course_id):
    """
    Start a course for a user and record the start date.
    """
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'User ID is required to start the course'}), 400

        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        study_material = StudyMaterial.query.get_or_404(course_id)

        # Ensure minimum_level is set (fallback to 1)
        required_level = study_material.minimum_level or 1

        if not isinstance(required_level, int):
            try:
                required_level = int(required_level)
            except Exception:
                required_level = 1

        # Check access eligibility
        if not can_access_level(user, required_level):
            return jsonify({'error': f"You must complete earlier levels to access this course."}), 403

        # Check if already started
        user_progress = UserProgress.query.filter_by(
            user_id=user_id,
            study_material_id=course_id
        ).first()

        if user_progress:
            logging.info(f"User {user_id} has already started course {course_id}.")
            return jsonify({
                'message': 'Course already started',
                'redirect_url': url_for('study_material_routes.view_course', course_id=course_id)
            }), 200

        # Start new course progress
        user_progress = UserProgress(
            user_id=user_id,
            study_material_id=course_id,
            pages_visited=0,
            progress_percentage=0,
            completion_date=None,
            start_date=datetime.utcnow()
        )
        db.session.add(user_progress)
        db.session.commit()

        logging.info(f"User {user_id} started course {course_id} at {user_progress.start_date}.")
        return jsonify({
            'success': 'Course started successfully',
            'start_date': user_progress.start_date.isoformat(),
            'redirect_url': url_for('study_material_routes.view_course', course_id=course_id)
        }), 201

    except Exception as e:
        logging.error(f"Error starting course: {e}", exc_info=True)
        return jsonify({'error': 'Failed to start course'}), 500

def can_access_level(user, level_id):
    """
    Check if the user can access the specified level.
    """
    try:
        user_level = user.get_current_level() if hasattr(user, 'get_current_level') else user.current_level or 0
        level_id = level_id or 1

        # Allow access if user's current level is high enough
        if user_level >= level_id:
            return True

        # Allow access if the user's designation permits
        if user.designation and user.designation.starting_level <= level_id:
            return True

        # Check if all areas in the previous level are completed
        previous_level_id = level_id - 1
        if previous_level_id > 0:
            areas = Area.query.all()
            for area in areas:
                progress = UserLevelProgress.query.filter_by(
                    user_id=user.id,
                    level_id=previous_level_id,
                    area_id=area.id,
                    passed=True
                ).first()
                if not progress:
                    return False

        return True

    except Exception as e:
        logging.warning(f"Access level check failed: {e}")
        return False

@study_material_routes.route('/view_course/<int:course_id>', methods=['GET'])
def view_course(course_id):
    """
    Display the details of a study material course with inline files.
    """
    try:
        # Fetch study material details
        logging.info(f"Fetching study material for course_id: {course_id}")
        study_material = StudyMaterial.query.get_or_404(course_id)
        logging.info(f"Study material fetched: {study_material.title}")

        # Fetch associated subtopics
        subtopics = SubTopic.query.filter_by(study_material_id=course_id).all()
        logging.info(f"Subtopics fetched: {[subtopic.title for subtopic in subtopics]}")

        # Fetch user ID securely from session
        user_id = session.get('user_id')
        if not user_id:
            raise ValueError("User ID not found in session.")
        logging.info(f"User ID fetched from session: {user_id}")

        # Fetch user details
        user = User.query.get(user_id)
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for('auth_routes.login'))

        # Get the course level restriction (if any)
        level_id = study_material.restriction_level or 0

        # Check level access
        if not can_access_level(user, level_id):
            flash(f"Complete the previous levels before accessing Level {level_id}.", "danger")
            return redirect(url_for('study_material_routes.list_study_materials'))

        # Fetch or initialize user progress
        user_progress = UserProgress.query.filter_by(
            user_id=user_id,
            study_material_id=course_id
        ).first()

        if not user_progress:
            logging.info(f"No progress found for user_id {user_id}. Initializing progress...")
            user_progress = UserProgress(
                user_id=user_id,
                study_material_id=course_id,
                pages_visited=0,
                progress_percentage=0,
                start_date=datetime.utcnow()
            )
            db.session.add(user_progress)
            db.session.commit()
            logging.info(f"User progress initialized for course_id {course_id} and user_id {user_id}")

        # Fetch files from MongoDB using file references stored in PostgreSQL
        documents = []

        if study_material.files:
            logging.info(f"Fetching documents associated with study_material.files: {study_material.files}")

            # study_material.files is a list of strings, each like "mongo_id|filename"
            for file_entry in study_material.files:
                try:
                    if "|" not in file_entry:
                        logging.warning(f"Skipping malformed file entry: {file_entry}")
                        continue

                    file_id, filename = file_entry.split("|", 1)  # split once, in case filename has "|"
                    file_id = file_id.strip()
                    filename = filename.strip()

                    if not file_id:
                        logging.warning(f"Skipping file with empty file_id: {filename}")
                        continue

                    grid_file = grid_fs.get(ObjectId(file_id))

                    file_data = {
                        'filename': filename,
                        'id': str(grid_file._id),
                        'type': None,
                    }

                    # Determine file type for rendering
                    if filename.lower().endswith('.txt'):
                        file_data['content'] = grid_file.read().decode('utf-8', errors='ignore')
                        file_data['type'] = 'text'
                    elif filename.lower().endswith('.pdf'):
                        file_data['type'] = 'pdf'
                    elif filename.lower().endswith('.pptx'):
                        file_data['type'] = 'pptx'
                    elif filename.lower().endswith('.docx'):
                        file_data['type'] = 'docx'
                    else:
                        file_data['type'] = 'unsupported'

                    documents.append(file_data)
                    logging.info(f"File added to documents list: {file_data['filename']}")

                except Exception as e:
                    logging.warning(f"Error retrieving file {file_entry} from GridFS: {e}")

        # Process subtopics to include metadata if available
        for subtopic in subtopics:
            try:
                if subtopic.file_id:
                    grid_file = grid_fs.get(ObjectId(subtopic.file_id))
                    subtopic.file_metadata = {
                        'filename': grid_file.filename,
                        'id': str(grid_file._id),
                        'type': 'unknown',
                    }
                    filename_lower = grid_file.filename.lower()
                    if filename_lower.endswith('.txt'):
                        subtopic.file_metadata['type'] = 'text'
                    elif filename_lower.endswith('.pdf'):
                        subtopic.file_metadata['type'] = 'pdf'
                    elif filename_lower.endswith('.pptx'):
                        subtopic.file_metadata['type'] = 'pptx'
                    elif filename_lower.endswith('.docx'):
                        subtopic.file_metadata['type'] = 'docx'
                else:
                    subtopic.file_metadata = None

                logging.info(f"Subtopic processed: {subtopic.title} with file metadata: {subtopic.file_metadata}")
            except Exception as e:
                logging.warning(f"Error processing subtopic file {subtopic.file_id}: {e}")

        # Render the course details page
        logging.info(f"Rendering course page with {len(documents)} documents and {len(subtopics)} subtopics.")
        return render_template(
            'view_course.html',
            study_material=study_material,
            subtopics=subtopics,
            documents=documents,
            user_progress=user_progress
        )

    except ValueError as ve:
        logging.error(f"Validation error: {ve}")
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        logging.error(f"Error viewing course for course_id={course_id}: {e}")
        return jsonify({'error': f'An error occurred while fetching course details: {str(e)}'}), 500

@study_material_routes.route('/list', methods=['GET'])
def list_study_materials():
    """
    Render the list of all study materials with progress.
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_routes.login'))

    user = User.query.get(user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('auth_routes.login'))

    # Get all study materials
    materials = StudyMaterial.query.all()
    accessible_materials = []

    # Filter accessible study materials
    for material in materials:
        if can_access_study_material(user, material):
            accessible_materials.append(material)

    # Prepare progress data
    progress_data = []
    for material in accessible_materials:
        user_progress = UserProgress.query.filter_by(
            user_id=user.id,
            study_material_id=material.id
        ).first()
        progress_percentage = user_progress.progress_percentage if user_progress else 0
        progress_data.append({'course_id': material.id, 'progress_percentage': progress_percentage})

    return render_template('list_study_materials.html', materials=accessible_materials, progress_data=progress_data)


@study_material_routes.route('/upload_page', methods=['GET'])
def upload_page():
    """
    Render the upload page only for authorized users.
    """
    if session.get('is_super_admin') or session.get('role') == 'admin':
        return render_template('upload_study.html')
    
    # Log unauthorized access attempt
    logging.warning(f"Unauthorized access attempt to upload page by user ID: {session.get('user_id')}")
    
    # Redirect unauthorized users
    flash("You do not have permission to upload study materials.", "danger")
    return redirect(url_for('study_material_routes.dashboard'))


@study_material_routes.route('/study_materials', methods=['GET'])
def study_materials():
    """
    Render the Study Materials dashboard.
    """
    return render_template('study_materials.html')


@study_material_routes.route('/update_progress', methods=['POST'])
def update_progress():
    """
    Update the user's progress based on the pages visited for a specific study material.
    Check for level completion and update user level if necessary.
    """
    try:
        # Parse request data
        data = request.json
        user_id = session.get('user_id')  # Secure user identification
        study_material_id = data.get('study_material_id')
        current_page = data.get('current_page')
        total_pages = data.get('total_pages')

        # Validate input
        if not user_id or not study_material_id or current_page is None or total_pages is None:
            logging.error("Invalid data provided for progress update.")
            return jsonify({'error': 'Invalid input data'}), 400

        # Fetch the study material to ensure it exists and get the total pages
        study_material = StudyMaterial.query.get(study_material_id)
        if not study_material:
            logging.error(f"Study material with ID {study_material_id} not found.")
            return jsonify({'error': 'Study material not found'}), 404

        # Ensure total pages from the client matches the database record
        if total_pages != study_material.total_pages:
            logging.warning(f"Total pages mismatch: client {total_pages} != DB {study_material.total_pages}")
            total_pages = study_material.total_pages

        # Guard Clause: Prevent division by zero
        if total_pages < 1:
            logging.error("Total pages is zero, cannot update progress.")
            return jsonify({'error': 'Study material has 0 pages. Please re-upload or fix the page count.'}), 400

        # Fetch or create the user's progress record
        user_progress = UserProgress.query.filter_by(
            user_id=user_id,
            study_material_id=study_material_id
        ).first()

        if not user_progress:
            user_progress = UserProgress(
                user_id=user_id,
                study_material_id=study_material_id,
                pages_visited=current_page,
                progress_percentage=int((current_page / total_pages) * 100)
            )
            db.session.add(user_progress)
        else:
            # Update progress only if the current page exceeds previously visited pages
            if current_page > user_progress.pages_visited:
                user_progress.pages_visited = current_page
                user_progress.progress_percentage = int((user_progress.pages_visited / total_pages) * 100)

        # Commit changes to update progress record
        db.session.commit()

        # Fetch the user record
        user = User.query.get(user_id)
        if not user:
            logging.error("User not found after progress update.")
            return jsonify({'error': 'User not found'}), 404

        # Derive current level from StudyMaterial's level_id
        current_level = study_material.level_id or 0
        
        # Guard Clause: Skip level checks for unrestricted materials
        if current_level == 0:
            logging.info("Unrestricted study material, skipping level checks.")
            return jsonify({'success': True, 'progress_percentage': user_progress.progress_percentage}), 200

        # Trigger Level Completion Check Only at 100% Progress
        if user_progress.progress_percentage >= 100:
            # Link Study Material Completion with Area Progress
            area = Area.query.filter_by(level_id=current_level).first()
            if area:
                progress = UserLevelProgress.query.filter_by(
                    user_id=user_id,
                    level_id=current_level,
                    area_id=area.id,
                    passed=True
                ).first()
                if not progress:
                    progress = UserLevelProgress(
                        user_id=user_id,
                        level_id=current_level,
                        area_id=area.id,
                        passed=True
                    )
                    db.session.add(progress)
                    db.session.commit()
                    logging.info(f"Marked area {area.id} as passed for user {user_id} at level {current_level}")

            # Check Level Completion and Update Level
            if has_completed_level(user_id, current_level):
                # Increment user's current level
                user.current_level = max(user.current_level, current_level + 1)
                db.session.commit()
                logging.info(f"User {user_id} level updated to {user.current_level}")
                flash(f"Congratulations! You have advanced to Level {user.current_level}", "success")
            else:
                logging.info(f"User {user_id} has not completed all areas in Level {current_level}")

        logging.info(f"Progress updated: user_id={user_id}, study_material_id={study_material_id}, "
                     f"pages_visited={user_progress.pages_visited}, progress_percentage={user_progress.progress_percentage}")

        return jsonify({'success': True, 'progress_percentage': user_progress.progress_percentage}), 200

    except KeyError as ke:
        logging.error(f"Missing key in the request data: {ke}")
        return jsonify({'error': f'Missing key: {str(ke)}'}), 400
    except Exception as e:
        logging.error(f"Error updating progress: {e}")
        return jsonify({'error': str(e)}), 500

def has_completed_level(user_id, level_id):
    """
    Check if the user has completed all areas in the specified level.
    """
    # Fetch areas specific to the level_id
    areas = Area.query.filter_by(level_id=level_id).all()
    for area in areas:
        progress = UserLevelProgress.query.filter_by(
            user_id=user_id,
            level_id=level_id,
            area_id=area.id,
            passed=True
        ).first()
        if not progress:
            return False
    return True


@study_material_routes.route('/stream_file/<file_id>', methods=['GET'])
def stream_file(file_id):
    """
    Stream file content for inline display.
    """
    try:
        # Retrieve the file from GridFS
        grid_file = grid_fs.get(ObjectId(file_id))
        if not grid_file:
            logging.error(f"File with ID {file_id} not found in GridFS.")
            return jsonify({'error': 'File not found'}), 404

        # Determine the correct content type based on file extension
        filename = grid_file.filename
        extension = f".{filename.rsplit('.', 1)[-1].lower()}"
        content_type_map = {
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain'
        }
        content_type = content_type_map.get(extension, None)

        if not content_type:
            logging.warning(f"Unsupported file type for file ID {file_id}: {filename}")
            return jsonify({'error': f'Unsupported file type: {extension}'}), 400

        # Stream the file content in chunks
        def generate():
            try:
                while chunk := grid_file.read(8192):  # Read in 8KB chunks
                    yield chunk
            except Exception as e:
                logging.error(f"Error reading file ID {file_id} in chunks: {e}")
                raise

        # Prepare the response with appropriate headers
        response = make_response(generate())
        response.headers['Content-Type'] = content_type
        response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        response.headers['Cache-Control'] = 'no-store'  # Prevent caching
        response.headers['Content-Security-Policy'] = "frame-ancestors 'self'; script-src 'self';"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'

        logging.info(f"Successfully streamed file with ID {file_id} ({filename})")
        return response

    except FileNotFoundError:
        logging.error(f"File with ID {file_id} does not exist in GridFS.")
        return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        logging.error(f"Error streaming file with ID {file_id}: {e}")
        return jsonify({'error': f'Failed to stream file: {str(e)}'}), 500


@study_material_routes.route('/update_time', methods=['POST'])
def update_time():
    try:
        data = request.json
        elapsed_time = data.get('elapsed_time', 0)
        user_progress = UserProgress.query.filter_by(
            user_id=session.get('user_id'), 
            study_material_id=data.get('study_material_id')
        ).first()

        if not user_progress:
            return jsonify({'error': 'Progress record not found'}), 404

        # Ensure start_date exists to calculate max_allowed_time
        if not user_progress.start_date:
            return jsonify({'error': 'Start date not available for progress tracking'}), 400

        max_allowed_time = (datetime.utcnow() - user_progress.start_date).total_seconds() + 300  # 5 min buffer
        if elapsed_time < 0 or elapsed_time > max_allowed_time:
            return jsonify({'error': 'Invalid elapsed_time value'}), 400

        user_progress.time_spent = (user_progress.time_spent or 0) + elapsed_time
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Error updating time: {e}")
        return jsonify({'error': str(e)}), 500
''
    

@study_material_routes.route('/download_file/<file_id>', methods=['GET'])
def download_file(file_id):
    """
    Provide a file download link to verify file fetching.
    """
    try:
        # Retrieve the file from GridFS
        grid_file = grid_fs.get(ObjectId(file_id))
        if not grid_file:
            logging.error(f"File with ID {file_id} not found in GridFS.")
            return jsonify({'error': 'File not found'}), 404

        # Create response with the file data
        filename = grid_file.filename
        response = make_response(grid_file.read())
        response.headers['Content-Type'] = 'application/octet-stream'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        logging.info(f"File downloaded successfully with ID {file_id} ({filename})")
        return response
    except Exception as e:
        logging.error(f"Error downloading file with ID {file_id}: {e}")
        return jsonify({'error': f'Failed to download file: {str(e)}'}), 500

@study_material_routes.route('/dashboard', methods=['GET'])
def dashboard():
    """
    Render the study materials dashboard with super admin access check.
    """
    # Ensure user is logged in
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_routes.login'))

    # Fetch is_super_admin directly from the database
    user = User.query.get(user_id)
    is_super_admin = user.is_super_admin if user else False

    # Pass the result to the template
    return render_template('dashboard.html', is_super_admin=is_super_admin)


def can_access_study_material(user, study_material):
    """
    Check if the user can access the study material based on designation and level.
    """
    # Get the restriction level for the study material
    restriction_level = study_material.restriction_level or 0

    # Allow access if no restriction level
    if restriction_level == 0:
        return True

    # Allow access if the user's designation allows skipping this level
    if user.designation and user.designation.starting_level <= restriction_level:
        return True

    # Allow access if the user has completed the previous level
    if restriction_level <= user.current_level:
        return True

    return False


@study_material_routes.route('/get_dropdowns', methods=['GET'])
def get_dropdowns():
    """
    Fetch Levels, Categories, and Designations for dropdowns
    """
    try:
        levels = Level.query.order_by(Level.level_number.asc()).all()
        categories = Category.query.order_by(Category.id.asc()).all()
        designations = Designation.query.order_by(Designation.id.asc()).all()

        # Constructing JSON response
        data = {
            "levels": [{"id": level.id, "number": level.level_number} for level in levels],
            "categories": [{"id": category.id, "name": category.name} for category in categories],
            "designations": [{"id": designation.id, "title": designation.title} for designation in designations]
        }

        return jsonify(data), 200
    except Exception as e:
        logging.error(f"Error fetching dropdowns: {e}")
        return jsonify({"error": "Failed to fetch dropdowns"}), 500

