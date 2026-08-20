"""Persistent conversation history for registered users.

Each user's conversations are stored in their own JSON file under
conversations/, keyed by conversation ID. This is the persistent counterpart
to guest_store.py, which holds guest conversations in memory only — see that
file's module docstring for why guests use a different, non-persistent store.
"""

import json
import os
import uuid
from datetime import datetime

CONV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversations")
os.makedirs(CONV_DIR, exist_ok=True)


def _conv_file(username):
    """Path to a given user's conversation file, one JSON file per user."""
    return os.path.join(CONV_DIR, f"{username}.json")


def _load(username):
    """Load a user's full conversation data, or an empty dict if they have none yet."""
    path = _conv_file(username)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _save(username, data):
    """Write a user's full conversation data back to their JSON file."""
    with open(_conv_file(username), "w") as f:
        json.dump(data, f, indent=2)


def create_conversation(username):
    """Start a new, empty conversation for this user and return its ID."""
    data = _load(username)
    conv_id = str(uuid.uuid4())[:8]
    data[conv_id] = {
        "id": conv_id,
        "title": "New conversation",
        "created_at": datetime.now().isoformat(),
        "messages": [],
    }
    _save(username, data)
    return conv_id


def add_message(username, conv_id, role, content, image=None, table=None):
    """Append one message to a conversation. Does nothing if the conversation
    doesn't exist (e.g. it was deleted mid-request).

    The conversation's title is set automatically from the first user message
    (truncated to 60 characters) — it only fires once, since it checks the
    title is still the placeholder "New conversation" before overwriting it.
    """
    data = _load(username)
    if conv_id not in data:
        return
    msg = {
        "role": role,
        "content": content,
        "image": image,
        "table": table,
        "timestamp": datetime.now().isoformat(),
    }
    data[conv_id]["messages"].append(msg)
    if role == "user" and data[conv_id]["title"] == "New conversation":
        data[conv_id]["title"] = content[:60]
    _save(username, data)


def get_conversations(username):
    """Return a summary of every conversation for this user (id, title,
    creation time, message count), most recent first. Used to populate the
    sidebar conversation list, not the full message content."""
    data = _load(username)
    summaries = [
        {
            "id": c["id"],
            "title": c["title"],
            "created_at": c["created_at"],
            "message_count": len(c["messages"]),
        }
        for c in data.values()
    ]
    return sorted(summaries, key=lambda x: x["created_at"], reverse=True)


def get_conversation(username, conv_id):
    """Return one full conversation, including all messages, or None if it doesn't exist."""
    data = _load(username)
    return data.get(conv_id)


def delete_conversation(username, conv_id):
    """Remove a single conversation. Silently does nothing if it doesn't exist."""
    data = _load(username)
    data.pop(conv_id, None)
    _save(username, data)


def clear_all_conversations(username):
    """Wipe every conversation this user has."""
    _save(username, {})
