"""REST session history can omit inline image data without mutating stored rows."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


DATA_URI = "data:image/png;base64," + "a" * 256
IMAGE_CONTENT = [
    {"type": "text", "text": "what is this?"},
    {"type": "image_url", "image_url": {"url": DATA_URI}},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from hermes_state import SessionDB

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_state.DEFAULT_DB_PATH", home / "state.db")

    db = SessionDB(db_path=home / "state.db")
    try:
        db.create_session(session_id="img-chat", source="desktop")
        db.append_messages_batch("img-chat", [
            {"role": "user", "content": IMAGE_CONTENT},
            {"role": "assistant", "content": "a chart"},
        ])
    finally:
        db.close()

    from hermes_cli.web_routers.sessions import manage_router

    app = FastAPI()
    app.include_router(manage_router)
    with TestClient(app) as test_client:
        yield test_client


def test_messages_default_keeps_inline_images(client):
    page = client.get("/api/sessions/img-chat/messages?limit=10&order=oldest").json()
    user_row = next(message for message in page["messages"] if message["role"] == "user")

    assert DATA_URI in str(user_row["content"])


def test_messages_can_replace_inline_images_with_placeholders(client):
    page = client.get(
        "/api/sessions/img-chat/messages?limit=10&order=oldest&inline_images=false"
    ).json()
    user_row = next(message for message in page["messages"] if message["role"] == "user")
    assistant_row = next(message for message in page["messages"] if message["role"] == "assistant")

    assert "[image]" in user_row["content"]
    assert DATA_URI not in user_row["content"]
    assert assistant_row["content"] == "a chart"


def test_lightweight_read_does_not_change_a_later_default_read(client):
    light = client.get(
        "/api/sessions/img-chat/messages?limit=10&order=oldest&inline_images=false"
    ).json()
    full = client.get("/api/sessions/img-chat/messages?limit=10&order=oldest").json()

    light_user = next(message for message in light["messages"] if message["role"] == "user")
    full_user = next(message for message in full["messages"] if message["role"] == "user")
    assert DATA_URI not in light_user["content"]
    assert DATA_URI in str(full_user["content"])
