import json
import os
import shutil
import time
import uuid
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

from chatbot import chatbot_reply

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")


def _load_users():
    if not os.path.exists(USERS_FILE):
        default = {"kinepik": {"password_hash": generate_password_hash("kinepik2024")}}
        with open(USERS_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(USERS_FILE) as f:
        return json.load(f)


def _save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _get_session_id():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def _conv_store():
    """Guest sessions use the in-memory store (never touches disk, cleared on
    logout/tab-close); registered users use the persistent JSON-file store."""
    if session.get("is_guest"):
        import guest_store
        return guest_store
    import conversation_store
    return conversation_store


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = _load_users()
        user = users.get(username)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["username"] = username
            return redirect(url_for("home"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not password:
            error = "Username and password are required."
        elif len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            users = _load_users()
            if username in users:
                error = "That username is already taken."
            else:
                users[username] = {"password_hash": generate_password_hash(password)}
                _save_users(users)
                session.clear()
                session["username"] = username
                return redirect(url_for("home"))

    return render_template("register.html", error=error)


@app.route("/guest")
def guest():
    """Start a temporary guest session — no account needed. Conversations
    live only in memory and are wiped on logout, tab close, or inactivity."""
    session.clear()
    session["username"] = f"guest_{uuid.uuid4().hex[:8]}"
    session["is_guest"] = True
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    if session.get("is_guest"):
        import guest_store
        guest_store.cleanup_guest(session["username"])
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/guest/cleanup", methods=["POST"])
def guest_cleanup():
    """Called via navigator.sendBeacon on tab close to wipe guest data immediately
    rather than waiting for the inactivity sweep in guest_store.py."""
    if session.get("is_guest") and session.get("username"):
        import guest_store
        guest_store.cleanup_guest(session["username"])
    return "", 204


# ── Main chat page ────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def home():
    return render_template(
        "index.html",
        username=session["username"],
        is_guest=session.get("is_guest", False),
    )


# ── Chat endpoint ─────────────────────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
@login_required
def chat():
    store = _conv_store()
    create_conversation, add_message = store.create_conversation, store.add_message

    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"reply": "No message provided."}), 400

    user_message = data["message"].strip()
    if not user_message:
        return jsonify({"reply": "Please enter a message."}), 400

    session_id = _get_session_id()
    username = session["username"]

    conv_id = session.get("conversation_id")
    if not conv_id:
        conv_id = create_conversation(username)
        session["conversation_id"] = conv_id

    add_message(username, conv_id, "user", user_message)

    try:
        reply = chatbot_reply(user_message, session_id=session_id)
        if not isinstance(reply, dict):
            reply = {"reply": reply, "image": None}

        image_path = reply.get("image")
        table_path = reply.get("table")
        persistent_image = None
        persistent_table = None

        if image_path:
            src = os.path.join(app.root_path, image_path)
            if os.path.exists(src):
                hist_dir = os.path.join(app.root_path, "static", "history")
                os.makedirs(hist_dir, exist_ok=True)
                unique_name = f"{conv_id}_{int(time.time())}.png"
                dst = os.path.join(hist_dir, unique_name)
                shutil.copy2(src, dst)
                persistent_image = f"static/history/{unique_name}"

        if table_path:
            src = os.path.join(app.root_path, table_path)
            if os.path.exists(src):
                hist_dir = os.path.join(app.root_path, "static", "history")
                os.makedirs(hist_dir, exist_ok=True)
                unique_name = f"{conv_id}_{int(time.time())}.html"
                dst = os.path.join(hist_dir, unique_name)
                shutil.copy2(src, dst)
                persistent_table = f"static/history/{unique_name}"

        add_message(
            username, conv_id, "bot", reply.get("reply", ""),
            persistent_image or image_path,
            table=persistent_table or table_path,
        )

        return jsonify({"reply": reply.get("reply", ""), "image": image_path, "table": table_path})

    except Exception as e:
        return jsonify({"reply": f"Error: {str(e)}"}), 500


# ── Conversation management ───────────────────────────────────────────────────

@app.route("/api/conversations", methods=["GET"])
@login_required
def list_conversations():
    return jsonify(_conv_store().get_conversations(session["username"]))


@app.route("/api/conversations/new", methods=["POST"])
@login_required
def new_conversation():
    from ai_router import clear_history
    old_session_id = session.get("session_id")
    if old_session_id:
        clear_history(old_session_id)
    session["session_id"] = str(uuid.uuid4())
    conv_id = _conv_store().create_conversation(session["username"])
    session["conversation_id"] = conv_id
    return jsonify({"conversation_id": conv_id})


@app.route("/api/conversations/<conv_id>", methods=["GET"])
@login_required
def load_conversation(conv_id):
    from ai_router import clear_history, _save_history
    conv = _conv_store().get_conversation(session["username"], conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404

    # Rebuild AI history from conversation messages
    old_session_id = session.get("session_id")
    if old_session_id:
        clear_history(old_session_id)
    new_session_id = str(uuid.uuid4())
    session["session_id"] = new_session_id
    session["conversation_id"] = conv_id

    ai_history = []
    for msg in conv["messages"]:
        role = "user" if msg["role"] == "user" else "assistant"
        ai_history.append({"role": role, "content": msg["content"]})
    _save_history(new_session_id, ai_history)

    return jsonify(conv)


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
@login_required
def delete_conversation(conv_id):
    _conv_store().delete_conversation(session["username"], conv_id)
    if session.get("conversation_id") == conv_id:
        session.pop("conversation_id", None)
    return jsonify({"status": "ok"})


@app.route("/api/conversations/clear-all", methods=["POST"])
@login_required
def clear_all():
    from ai_router import clear_history
    _conv_store().clear_all_conversations(session["username"])
    if session.get("session_id"):
        clear_history(session["session_id"])
    session.pop("conversation_id", None)
    return jsonify({"status": "ok"})


# ── Progress polling ──────────────────────────────────────────────────────────

@app.route("/api/progress", methods=["GET"])
@login_required
def api_progress():
    from ksea_analysis import _ksea_progress
    perturbation = request.args.get("perturbation", "").lower()
    cell_line = request.args.get("cell_line", "mcf7").lower()
    message = _ksea_progress.get((perturbation, cell_line))
    return jsonify({"progress": message})


@app.route("/api/perturbations", methods=["GET"])
@login_required
def api_perturbations():
    from kinepik_api import get_all_perturbations
    from utils import _normalise_records
    result = get_all_perturbations()
    records = _normalise_records(result)
    names = []
    for item in records:
        if isinstance(item, dict):
            name = (
                item.get("PerturbationName")
                or item.get("perturbation_name")
                or item.get("name")
                or item.get("Name")
            )
            if name:
                names.append(name)
    return jsonify({"perturbations": sorted(set(names), key=lambda x: x.lower())})


@app.route("/reset", methods=["POST"])
@login_required
def reset():
    from ai_router import clear_history
    if session.get("session_id"):
        clear_history(session["session_id"])
    session.pop("conversation_id", None)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5001)
