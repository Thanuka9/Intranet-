from flask import Blueprint, request, jsonify, render_template, url_for, make_response, session, redirect, flash
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from gridfs import GridFS
import logging
from bson.objectid import ObjectId
from models import db, StudyMaterial, SubTopic, UserProgress, User, Level, Area, UserLevelProgress, Designation, Category, LevelArea
from datetime import datetime
from io import BytesIO
import PyPDF2
from docx import Document
from pptx import Presentation
from PIL import Image, ImageDraw
from io import BytesIO
from utils.progress_utils import has_finished_study
import os
from dotenv import load_dotenv
from flask import current_app
from exams_routes import check_level_completion


# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize Blueprint
study_material_routes = Blueprint('study_material_routes', __name__)

# MongoDB Client and GridFS Initialization
# MongoDB + GridFS Initialization (from environment)
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
mongo_db_name = os.getenv("MONGO_DB_NAME", "collective_rcm")

mongo_client = MongoClient(mongo_uri)
mongo_db = mongo_client[mongo_db_name]
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

    Access is granted if:
      1. user.current_level >= level_id (progress-based)
      2. user.designation.starting_level >= level_id (designation-based)
      3. OR — if level_id <= 1, everyone may see level 1 by default.
    Otherwise, require that all LevelArea rules for the previous level are met:
      • 100% study completion for each area
      • Exam passed if one is required (but skips allowed by designation)
    """
    try:
        # normalize user’s current level
        user_level = (
            user.get_current_level()
            if hasattr(user, "get_current_level")
            else getattr(user, "current_level", 0)
        ) or 0

        required = level_id or 0

        # 1) Progress-based
        if user_level >= required:
            return True

        # 2) Designation-based
        if (
            user.designation
            and getattr(user.designation, "starting_level", 0) >= required
        ):
            return True

        # 3) Level 1 is open to all
        if required <= 1:
            return True

        # 4) Gated by LevelArea entries for (required - 1)
        prev = required - 1
        level_areas = LevelArea.query.filter_by(level_id=prev).all()
        for la in level_areas:
            # a) study must be 100% complete
            if not has_finished_study(user.id, prev, la.area_id):
                return False

            # b) if an exam is specified, it must be passed (unless skipped)
            if la.required_exam_id:
                # skip only if designation allows
                if user.can_skip_exam(la.required_exam):
                    continue

                prog = (
                    UserLevelProgress.query
                    .filter_by(
                        user_id=user.id,
                        level_id=prev,
                        area_id=la.area_id,
                        passed=True
                    )
                    .first()
                )
                if not prog:
                    return False

        return True

    except Exception as e:
        logging.warning(f"Access level check failed: {e}")
        return False

# ----  Course Details  ----
@study_material_routes.route("/view_course/<int:course_id>")
def view_course(course_id):
    """
    Dashboard-style page that shows title, description,
    dates, overall progress, and a “Continue” link.
    No heavy file streaming happens here.
    """
    # 1 Fetch objects --------------------------------------------------
    study_material = StudyMaterial.query.get_or_404(course_id)
    subtopics      = SubTopic.query.filter_by(study_material_id=course_id).all()

    user_id = session.get("user_id")
    if not user_id:
        flash("Please log in.", "warning")
        return redirect(url_for("auth_routes.login"))

    user         = User.query.get_or_404(user_id)
    level_id     = study_material.restriction_level or 0
    if not can_access_level(user, level_id):
        flash(f"Complete previous levels to unlock Level {level_id}.", "danger")
        return redirect(url_for("study_material_routes.list_study_materials"))

    user_progress = (UserProgress.query
                     .filter_by(user_id=user_id, study_material_id=course_id)
                     .first())

    if not user_progress:
        user_progress = UserProgress(
            user_id=user_id,
            study_material_id=course_id,
            pages_visited=0,
            progress_percentage=0,
            start_date=datetime.utcnow()
        )
        db.session.add(user_progress)
        db.session.commit()

    # 2 Pick the first PDF-id so the template can build the CTA
    first_doc_id = None
    if study_material.files:
        head_entry = study_material.files[0]
        if "|" in head_entry:
            first_doc_id, _ = head_entry.split("|", 1)

    continue_url = url_for("study_material_routes.course_content",
                           course_id=course_id,
                           file_id=first_doc_id) if first_doc_id else None

    # 3 Render
    return render_template(
        "view_course.html",
        study_material=study_material,
        subtopics=subtopics,
        user_progress=user_progress,
        continue_url=continue_url
    )

# ----  Document Viewer  --------------------------------------------
@study_material_routes.route("/course_content/<int:course_id>")
def course_content(course_id):
    """
    Streams PDFs / other docs in a dedicated viewer.
    ?file_id=<mongo-id> tells the page which file to open first.
    """

    user_id = session.get("user_id")
    if not user_id:
        flash("Please log in.", "warning")
        return redirect(url_for("auth_routes.login"))

    study_material = StudyMaterial.query.get_or_404(course_id)

    # ---- Collect docs ------------------------------------------------
    requested_id = request.args.get("file_id")          # <<< NEW
    documents = []

    for entry in (study_material.files or []):
        if "|" not in entry:
            continue
        fid, filename = (p.strip() for p in entry.split("|", 1))
        try:
            gfile = grid_fs.get(ObjectId(fid))
        except Exception as e:
            current_app.logger.warning(f"GridFS fetch failed: {e}")
            continue

        ext = filename.lower().rsplit(".", 1)[-1]
        doc_type = ext if ext in ("pdf", "pptx", "docx", "txt") else "unsupported"

        documents.append({
            "id": str(gfile._id),
            "filename": filename,
            "type": doc_type,
            "content": gfile.read().decode() if doc_type == "txt" else None
        })

    # ---- Put the requested file first -------------------------------  <<< NEW
    if requested_id:
        documents.sort(key=lambda d: 0 if d["id"] == requested_id else 1)

    # ---- Progress record (unchanged) --------------------------------
    user_progress = (UserProgress.query
                     .filter_by(user_id=user_id, study_material_id=course_id)
                     .first())

    return render_template(
        "course_content.html",
        study_material=study_material,
        documents=documents,
        user_progress=user_progress
    )

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

    # --- SORT by Course ID ---
    # Zip, sort, and unzip to keep both lists in sync
    combined = sorted(zip(accessible_materials, progress_data), key=lambda x: x[0].id)
    if combined:
        accessible_materials, progress_data = zip(*combined)
        accessible_materials, progress_data = list(accessible_materials), list(progress_data)
    else:
        accessible_materials, progress_data = [], []

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


@study_material_routes.route("/update_progress", methods=["POST"])
def update_progress():
    """
    Called by the viewer whenever a page becomes 50 % visible.
    Updates pages_visited, progress %, completion_date, and (optionally) bumps the user level.
    """
    try:
        data              = request.json or {}
        user_id           = session.get("user_id")
        study_material_id = data.get("study_material_id")
        current_page      = int(data.get("current_page", 0))
        total_pages       = int(data.get("total_pages", 0))

        if not (user_id and study_material_id and total_pages):
            return jsonify(error="invalid input"), 400

        study_material = StudyMaterial.query.get_or_404(study_material_id)
        if total_pages != study_material.total_pages:
            total_pages = study_material.total_pages  # always trust DB

        prog = (UserProgress.query
                .filter_by(user_id=user_id, study_material_id=study_material_id)
                .with_for_update()
                .first())

        if not prog:
            prog = UserProgress(
                user_id=user_id,
                study_material_id=study_material_id,
                pages_visited=current_page,
                start_date=datetime.utcnow()
            )
            db.session.add(prog)

        # advance page counter
        if current_page > prog.pages_visited:
            prog.pages_visited = current_page

        # compute %
        prog.progress_percentage = int(prog.pages_visited / total_pages * 100)

        # stamp completion once
        if prog.progress_percentage == 100 and prog.completion_date is None:
            prog.completion_date = datetime.utcnow()
            prog.completed = True

        db.session.commit()

        # ---------- level-unlock check --------------------------------
        current_level = study_material.level_id or 0
        if current_level and prog.completed:
            # only advance when *all* areas + exams for this level are satisfied
            if check_level_completion(user_id, current_level):
                user = User.query.get(user_id)
                user.current_level = current_level + 1
                db.session.commit()
                flash(f"🎉 Level {current_level + 1} unlocked!", "success")

        return jsonify(
            success=True,
            progress_percentage=prog.progress_percentage,
            completed=prog.completed
        ), 200

    except Exception as e:
        logging.exception("update_progress failed")
        return jsonify(error=str(e)), 500



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
    """
    Add elapsed seconds to UserProgress.total_time.
    The viewer sends chunks (default 30 s) while the tab is visible.
    """
    try:
        data         = request.json or {}
        delta        = int(data.get('elapsed_time', 0))
        material_id  = data.get('study_material_id')
        user_id      = session.get('user_id')

        if not user_id or not material_id:
            return jsonify(error="missing ids"), 400
        if delta <= 0:
            return jsonify(success=True)          # ignore zero/neg chunks

        prog = (UserProgress.query
                .filter_by(user_id=user_id, study_material_id=material_id)
                .with_for_update()
                .first())

        if not prog:
            return jsonify(error="progress not found"), 404

        # sanity-check: don’t let a single chunk exceed total possible time.
        if prog.start_date:
            max_allowed = (datetime.utcnow() - prog.start_date).total_seconds() + 300
            if delta > max_allowed:
                return jsonify(error="elapsed_time too large"), 400

        prog.time_spent = (prog.time_spent or 0) + delta
        db.session.commit()
        return jsonify(success=True)

    except Exception as e:
        logging.exception("update_time failed")
        return jsonify(error=str(e)), 500


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
    Check if the user can access the study material based on the study material's 
    restriction level, the user's designation, and the user's current level.
    
    A None restriction_level is treated as 0.
    """
    # Use a fallback of 0 if the restriction_level is None.
    restriction_level = study_material.restriction_level or 0
    # Retrieve the user's current level using the helper method.
    user_level = user.get_current_level()

    # If there's no restriction level, allow access.
    if restriction_level == 0:
        return True

    # If the user's designation allows skipping this level, allow access.
    if user.designation and user.designation.starting_level >= restriction_level:
        return True

    # If the user's current level meets or exceeds the restriction, allow access.
    if restriction_level <= user_level:
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

