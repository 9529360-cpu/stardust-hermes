from pathlib import Path

import pytest

from hermes_cli import contacts_db as contacts


@pytest.fixture
def contact_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def test_remember_and_lookup_by_alias_and_channel(contact_home):
    with contacts.connect_closing() as conn:
        person = contacts.remember_contact(
            conn,
            display_name="王强",
            aliases=["老王", "Wang Qiang"],
            relationship="同事",
            channels={"wechat": "wxid_wang", "email": "wang@example.com"},
        )
        assert person.id.startswith("c_")
        assert person.relationship == "同事"
        assert {item.channel for item in person.channels} == {"wechat", "email"}

        by_alias = contacts.lookup_contact(conn, " 老王 ")
        assert by_alias is not None and by_alias.id == person.id
        assert contacts.lookup_contact(conn, "老王", channel="wechat").id == person.id
        assert contacts.lookup_contact(conn, "老王", channel="slack") is None


def test_remember_same_alias_updates_contact_without_duplicate(contact_home):
    with contacts.connect_closing() as conn:
        original = contacts.remember_contact(
            conn, display_name="王强", aliases=["老王"], relationship="同事",
            channels={"wechat": "wx-old"},
        )
        updated = contacts.remember_contact(
            conn, display_name="王强", aliases=["老王", "王工"], relationship="项目负责人",
            channels={"wechat": "wx-new"},
        )

        assert updated.id == original.id
        assert updated.relationship == "项目负责人"
        assert "王工" in updated.aliases
        assert [(item.channel, item.handle) for item in updated.channels] == [("wechat", "wx-new")]
        assert len(contacts.list_contacts(conn)) == 1


def test_alias_collision_fails_atomically(contact_home):
    with contacts.connect_closing() as conn:
        first = contacts.remember_contact(conn, display_name="王强", aliases=["老王"])
        second = contacts.remember_contact(conn, display_name="王伟", aliases=["王伟"])

        with pytest.raises(ValueError, match="already belongs|multiple existing"):
            contacts.remember_contact(conn, display_name="王伟", aliases=["老王", "王伟"])

        assert contacts.lookup_contact(conn, "老王").id == first.id
        assert contacts.lookup_contact(conn, "王伟").id == second.id


def test_channel_handle_cannot_be_claimed_by_two_contacts(contact_home):
    with contacts.connect_closing() as conn:
        first = contacts.remember_contact(
            conn, display_name="Alice", channels={"email": "shared@example.com"}
        )
        contacts.remember_contact(conn, display_name="Bob")

        with pytest.raises(ValueError, match="already belongs"):
            contacts.remember_contact(
                conn, display_name="Bob", channels={"email": "shared@example.com"}
            )

        assert contacts.lookup_contact(conn, "Alice").id == first.id
        assert contacts.lookup_contact(conn, "Bob").channels == ()


def test_list_filters_alias_substring_and_channel(contact_home):
    with contacts.connect_closing() as conn:
        contacts.remember_contact(
            conn, display_name="王强", aliases=["老王"], channels={"wechat": "wx1"}
        )
        contacts.remember_contact(
            conn, display_name="李梅", aliases=["小李"], channels={"email": "li@example.com"}
        )

        assert [item.display_name for item in contacts.list_contacts(conn, query="王")] == ["王强"]
        assert [item.display_name for item in contacts.list_contacts(conn, channel="email")] == ["李梅"]


def test_archive_is_soft_and_hidden_from_normal_lookup(contact_home):
    with contacts.connect_closing() as conn:
        person = contacts.remember_contact(conn, display_name="王强", aliases=["老王"])
        assert contacts.archive_contact(conn, person.id) is True
        assert contacts.archive_contact(conn, person.id) is False
        assert contacts.lookup_contact(conn, "老王") is None
        archived = contacts.get_contact(conn, person.id, include_archived=True)
        assert archived is not None and archived.archived is True


def test_contacts_are_profile_scoped(tmp_path, monkeypatch):
    first_home = tmp_path / "profile-a"
    second_home = tmp_path / "profile-b"
    first_home.mkdir()
    second_home.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(first_home))
    with contacts.connect_closing() as conn:
        contacts.remember_contact(conn, display_name="Only A", aliases=["friend"])

    monkeypatch.setenv("HERMES_HOME", str(second_home))
    with contacts.connect_closing() as conn:
        assert contacts.lookup_contact(conn, "friend") is None
        contacts.remember_contact(conn, display_name="Only B", aliases=["friend"])

    monkeypatch.setenv("HERMES_HOME", str(first_home))
    with contacts.connect_closing() as conn:
        assert contacts.lookup_contact(conn, "friend").display_name == "Only A"
