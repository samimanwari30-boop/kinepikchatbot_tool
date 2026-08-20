"""In-memory conversation store for guest sessions.

Mirrors conversation_store.py's function signatures exactly, so app.py can
swap between the two based on whether the current session is a guest —
but nothing here ever touches disk. Guest data lives only in this process's
memory and is gone on cleanup_guest(), app restart, or a stale-session sweep.
"""

import uuid
from datetime import datetime, timedelta

# {guest_id: {"conversations": {conv_id: {...}}, "last_active": datetime}}
_guest_data: dict[str, dict] = {}

MAX_GUEST_AGE = timedelta(hours=6)


def _touch(guest_id):
    """Get (creating if needed) a guest's in-memory entry, and refresh its
    last-active timestamp. Called at the start of every function here, since
    it doubles as both the lookup and the activity heartbeat used by
    _sweep_stale to decide which guests have gone idle."""
    entry = _guest_data.setdefault(guest_id, {"conversations": {}, "last_active": datetime.now()})
    entry["last_active"] = datetime.now()
    return entry


def _sweep_stale():
    """Drop guest sessions that have been inactive too long, so memory doesn't
    grow unbounded if a tab-close cleanup call is ever missed."""
    cutoff = datetime.now() - MAX_GUEST_AGE
    stale = [gid for gid, entry in _guest_data.items() if entry["last_active"] < cutoff]
    for gid in stale:
        _guest_data.pop(gid, None)


def create_conversation(guest_id):
    """Start a new, empty conversation for this guest and return its ID."""
    _sweep_stale()
    entry = _touch(guest_id)
    conv_id = str(uuid.uuid4())[:8]
    entry["conversations"][conv_id] = {
        "id": conv_id,
        "title": "New conversation",
        "created_at": datetime.now().isoformat(),
        "messages": [],
    }
    return conv_id


def add_message(guest_id, conv_id, role, content, image=None, table=None):
    """Append one message to a guest conversation. Does nothing if the
    conversation doesn't exist. Auto-titles the conversation from the first
    user message, same behaviour as conversation_store.py's version."""
    entry = _touch(guest_id)
    conversations = entry["conversations"]
    if conv_id not in conversations:
        return
    msg = {
        "role": role,
        "content": content,
        "image": image,
        "table": table,
        "timestamp": datetime.now().isoformat(),
    }
    conversations[conv_id]["messages"].append(msg)
    if role == "user" and conversations[conv_id]["title"] == "New conversation":
        conversations[conv_id]["title"] = content[:60]


def get_conversations(guest_id):
    """Return a summary of every conversation for this guest (id, title,
    creation time, message count), most recent first."""
    entry = _touch(guest_id)
    summaries = [
        {
            "id": c["id"],
            "title": c["title"],
            "created_at": c["created_at"],
            "message_count": len(c["messages"]),
        }
        for c in entry["conversations"].values()
    ]
    return sorted(summaries, key=lambda x: x["created_at"], reverse=True)


def get_conversation(guest_id, conv_id):
    """Return one full guest conversation, including all messages, or None if it doesn't exist."""
    entry = _touch(guest_id)
    return entry["conversations"].get(conv_id)


def delete_conversation(guest_id, conv_id):
    """Remove a single conversation. Silently does nothing if it doesn't exist."""
    entry = _touch(guest_id)
    entry["conversations"].pop(conv_id, None)


def clear_all_conversations(guest_id):
    """Wipe every conversation this guest has, without removing the guest
    entry itself (unlike cleanup_guest, which removes everything)."""
    entry = _touch(guest_id)
    entry["conversations"] = {}


def cleanup_guest(guest_id):
    """Fully remove a guest's data — called on logout and on tab-close beacon."""
    _guest_data.pop(guest_id, None)
