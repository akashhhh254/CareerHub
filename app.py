import os
print("DATABASE_URL =", os.getenv("DATABASE_URL"))
import json
from pathlib import Path
from flask import Flask, session, redirect, request, render_template, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from db import engine, base, SessionLocal
import models
import PyPDF2
import docx
from ai import analyze_resume

app = Flask(__name__)
# SECRET_KEY should live in .env in real use. We fall back to a random key
# so the app still runs if it's missing, but sessions won't persist across
# restarts in that case -- set SECRET_KEY in .env for real use.
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24).hex())

# Reject uploads bigger than 5 MB before they ever reach our code.
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".docx"}

base.metadata.create_all(bind=engine)


# ---------- helpers ----------

def get_current_user(db):
    """Look up the logged-in user's row, or None."""
    if "user" not in session:
        return None
    return db.query(models.User).filter(models.User.username == session["user"]).first()


# ---------- routes ----------

@app.route("/")
def home():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        db = SessionLocal()
        try:
            email = (request.form.get("email") or "").strip().lower()
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""

            if not email or not username or not password:
                flash("Please fill in username, email and password.", "error")
                return render_template("signup.html")

            if len(password) < 6:
                flash("Password must be at least 6 characters long.", "error")
                return render_template("signup.html")

            existing = db.query(models.User).filter(
                (models.User.email == email) | (models.User.username == username)
            ).first()
            if existing:
                flash("An account with that email or username already exists.", "error")
                return render_template("signup.html")

            user = models.User(
                email=email,
                username=username,
                password=generate_password_hash(password),
            )
            db.add(user)
            db.commit()
            flash("Account created! Please log in.", "success")
            return redirect(url_for("login"))
        finally:
            db.close()

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = SessionLocal()
        try:
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""

            user = db.query(models.User).filter(models.User.email == email).first()
            if user and check_password_hash(user.password, password):
                session["user"] = user.username
                return redirect(url_for("dashboard"))

            flash("Invalid email or password.", "error")
            return render_template("login.html")
        finally:
            db.close()

    return render_template("login.html")


@app.route("/forget", methods=["GET", "POST"])
def forget():
    # NOTE: no email service is configured, so this does not actually send
    # a reset email. It's here so the page is functional/navigable and
    # doesn't 404. Wire up a real mail provider (e.g. Flask-Mail) if you
    # want this to work for real.
    if request.method == "POST":
        flash(
            "If an account exists for that email, a reset link would be sent. "
            "(Email sending isn't configured yet in this project.)",
            "success",
        )
        return redirect(url_for("login"))
    return render_template("forget.html")


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    result = None

    if request.method == "POST":
        user_goal = (request.form.get("desired_role") or "").strip()
        resume_text = (request.form.get("resume") or "").strip()
        file = request.files.get("resume_file")

        if file and file.filename:
            _, ext = os.path.splitext(file.filename.lower())
            if ext not in ALLOWED_EXTENSIONS:
                result = {"error": "Unsupported file type. Please upload a PDF or DOCX file."}
            elif ext == ".pdf":
                try:
                    reader = PyPDF2.PdfReader(file)
                    pages_text = [page.extract_text() or "" for page in reader.pages]
                    resume_text = "\n".join(pages_text).strip()
                except Exception as e:
                    result = {"error": f"Error reading PDF file: {str(e)}"}
            elif ext == ".docx":
                try:
                    doc = docx.Document(file)
                    resume_text = "\n".join(p.text for p in doc.paragraphs).strip()
                except Exception as e:
                    result = {"error": f"Error reading DOCX file: {str(e)}"}

        if result is None:
            if not resume_text:
                result = {"error": "Please paste your resume text or upload a PDF/DOCX file."}
            elif not user_goal:
                result = {"error": "Please tell us the role/goal you're aiming for."}
            else:
                result = analyze_resume(resume_text, user_goal)

                if "error" not in result:
                    db = SessionLocal()
                    try:
                        user = get_current_user(db)
                        report = models.Report(
                            user_id=user.id,
                            resume_text=resume_text,
                            user_goal=user_goal,
                            result=json.dumps(result),
                        )
                        db.add(report)
                        db.commit()
                    finally:
                        db.close()

    return render_template("dashboard.html", user=session["user"], result=result)


@app.route("/history")
def history():
    if "user" not in session:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        user = get_current_user(db)
        reports = (
            db.query(models.Report)
            .filter(models.Report.user_id == user.id)
            .order_by(models.Report.id.desc())
            .all()
        )

        passed_reports = []
        for r in reports:
            try:
                result = json.loads(r.result)
            except (json.JSONDecodeError, TypeError):
                result = {"error": "Invalid JSON format"}
            passed_reports.append({
                "resume_text": r.resume_text,
                "user_goal": r.user_goal,
                "result": result,
            })
    finally:
        db.close()

    return render_template("history.html", reports=passed_reports)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
