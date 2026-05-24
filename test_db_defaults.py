import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from telegram_manager import db


class FakeTable:
    def __init__(self, existing):
        self.existing = existing
        self.insert_payload = None
        self.update_payload = None

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.existing)

    def update(self, payload):
        self.update_payload = payload
        return self

    def insert(self, payload):
        self.insert_payload = payload
        return self


class RegisterAdminDefaultsTests(unittest.TestCase):
    def test_register_admin_inserts_indonesian_default_language_for_new_admins(self):
        table = FakeTable(existing=[])
        client = Mock()
        client.table.return_value = table

        with patch.object(db, "_get_client", return_value=client):
            db.register_admin(123, "user", "Name")

        self.assertEqual(table.insert_payload["lang"], "id")

    def test_register_admin_does_not_overwrite_existing_admin_language(self):
        table = FakeTable(existing=[{"user_id": 123}])
        client = Mock()
        client.table.return_value = table

        with patch.object(db, "_get_client", return_value=client):
            db.register_admin(123, "user", "Name")

        self.assertNotIn("lang", table.update_payload)


if __name__ == "__main__":
    unittest.main()
