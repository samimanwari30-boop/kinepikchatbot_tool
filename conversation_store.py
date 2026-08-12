import json
import os
import uuid
from datetime import datetime

CONV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversations")
os.makedirs(CONV_DIR, exist_ok=True)


def _conv_file(username):
    return os.path.join(CONV_DIR, f"{username}.json")


def _load(username):
    path = _conv_file(username)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _save(username, data):
    with open(_conv_file(username), "w") as f:
        json.dump(data, f, indent=2)


def create_conversation(username):
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
    data = _load(username)
    return data.get(conv_id)


def delete_conversation(username, conv_id):
    data = _load(username)
    data.pop(conv_id, None)
    _save(username, data)


def clear_all_conversations(username):
    _save(username, {})
