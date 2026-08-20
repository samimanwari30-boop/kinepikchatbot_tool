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
    """Load registered users from disk. On first run, creates the file with
    a single default account (username "kinepik") so there's always at least
    one way to log in."""
    if not os.path.exists(USERS_FILE):
        default = {"kinepik": {"password_hash": generate_password_hash("kinepik2024")}}
        with open(USERS_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(USERS_FILE) as f:
        return json.load(f)


def _save_users(users):
    """Write the full users dict back to disk."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def login_required(f):
    """Route decorator — redirects to the login page if there's no logged in
    user (registered or guest) in the session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _get_session_id():
    """Get this browser session's ID, creating one on first use. This is the
    key ai_router.py uses to keep separate conversation history per session,
    distinct from the conversation_id used for on-disk/in-memory storage."""
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
    """Login page. On POST, checks the submitted credentials against the
    stored password hash and starts a session if they match."""
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
    """Registration page. Validates username/password rules on POST, creates
    the account if the username isn't already taken, then logs the new user in."""
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
    """Log out. For guests, this also immediately wipes their in-memory data
    rather than waiting for the inactivity sweep."""
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
    """Main chat page. Requires login (registered or guest)."""
    return render_template(
        "index.html",
        username=session["username"],
        is_guest=session.get("is_guest", False),
    )


# ── Chat endpoint ─────────────────────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
@login_required
def chat():
    """Main chat endpoint. Saves the user's message, passes it to chatbot_reply
    (the actual AI pipeline), then saves and returns the response.

    Any image or table the reply generated lives in static/ under a filename
    that gets overwritten on the next query (e.g. static/ksea.png) — so before
    saving it to conversation history, it's copied to static/history/ under a
    unique name, otherwise loading an old conversation later would show
    whatever chart happens to be in static/ right now, not the one that was
    actually generated for that message.
    """
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
    """Return the sidebar's list of this user's conversations."""
    return jsonify(_conv_store().get_conversations(session["username"]))


@app.route("/api/conversations/new", methods=["POST"])
@login_required
def new_conversation():
    """Start a fresh conversation. Clears the AI's conversation history for
    the old session_id first, so the new conversation doesn't inherit
    context from whatever was discussed previously."""
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
    """Load a past conversation and make it the active one. Rebuilds the AI
    router's own history from the stored messages under a fresh session_id,
    so multi-turn context (e.g. "the top one") resolves correctly against
    this conversation's actual messages rather than whatever was in the
    previous session."""
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
    """Delete one conversation. If it was the active one, clear that too so
    the next message starts a fresh conversation instead of reusing a
    deleted ID."""
    _conv_store().delete_conversation(session["username"], conv_id)
    if session.get("conversation_id") == conv_id:
        session.pop("conversation_id", None)
    return jsonify({"status": "ok"})


@app.route("/api/conversations/clear-all", methods=["POST"])
@login_required
def clear_all():
    """Delete every conversation this user has, and clear the AI's session
    history and active conversation too, a full reset."""
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
    """Polled by the frontend while a full kinome scan is running, to show a
    live status message (e.g. which batch is in progress) instead of a
    silent, unexplained wait."""
    from ksea_analysis import _ksea_progress
    perturbation = request.args.get("perturbation", "").lower()
    cell_line = request.args.get("cell_line", "mcf7").lower()
    message = _ksea_progress.get((perturbation, cell_line))
    return jsonify({"progress": message})


@app.route("/api/perturbations", methods=["GET"])
@login_required
def api_perturbations():
    """Return the full sorted list of perturbation names KINEPIK has, used to
    populate an autocomplete/suggestion list in the frontend."""
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
    """Clear the AI's conversation history and detach from the current
    conversation, without deleting any stored conversations (unlike clear_all)."""
    from ai_router import clear_history
    if session.get("session_id"):
        clear_history(session["session_id"])
    session.pop("conversation_id", None)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5001)
