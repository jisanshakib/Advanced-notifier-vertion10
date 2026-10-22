"""PostgreSQL fault tests without credentials or a live database."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import psycopg


class DatabaseReconnectTests(unittest.TestCase):
    def setUp(self):
        self.old = MagicMock(closed=False)
        self.new = MagicMock(closed=False)
        self.new.execute.return_value.description = True
        self.new.execute.return_value.fetchall.return_value = [{"value": "saved"}]
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test.invalid/db"}), \
             patch.object(psycopg, "connect", return_value=self.old):
            spec = importlib.util.spec_from_file_location("db_under_test", Path(__file__).with_name("db.py"))
            self.db = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.db)
        self.old.reset_mock()

    def test_closed_connection_reconnects_before_query(self):
        self.old.closed = True
        with patch.object(self.db, "_connect", return_value=self.new) as connect:
            self.assertEqual(self.db.get_setting("owner"), "saved")
        connect.assert_called_once()
        self.new.execute.assert_called_once_with("SELECT value FROM settings WHERE key=%s", ("owner",))

    def test_dropped_idle_write_probe_recovers_without_duplicate_write(self):
        self.old.execute.side_effect = psycopg.OperationalError("idle disconnect")
        with patch.object(self.db, "_connect", return_value=self.new):
            self.db.toggle_priority(5)
        self.old.execute.assert_called_once_with("SELECT 1")
        self.new.execute.assert_called_once_with("UPDATE channels SET priority = 1 - priority WHERE id=%s", (5,))

    def test_read_retry_restores_saved_value(self):
        self.old.execute.side_effect = psycopg.InterfaceError("lost socket")
        with patch.object(self.db, "_connect", return_value=self.new):
            self.assertEqual(self.db.get_setting("owner"), "saved")

    def test_ambiguous_write_not_replayed(self):
        self.old.execute.side_effect = [MagicMock(), psycopg.OperationalError("lost acknowledgement")]
        with patch.object(self.db, "_connect") as connect:
            with self.assertRaises(psycopg.OperationalError):
                self.db.toggle_priority(5)
        connect.assert_not_called()
        self.assertEqual(self.old.execute.call_count, 2)  # probe + ONE write
        self.old.close.assert_called_once()

    def test_second_read_failure_is_bounded(self):
        self.old.execute.side_effect = psycopg.OperationalError("offline")
        self.new.execute.side_effect = psycopg.OperationalError("still offline")
        with patch.object(self.db, "_connect", return_value=self.new) as connect:
            with self.assertRaises(psycopg.OperationalError):
                self.db.get_setting("owner")
        connect.assert_called_once()

    def test_connect_retries_three_times_without_logging_credentials(self):
        with patch.object(self.db, "_connect_once", side_effect=[psycopg.OperationalError(),
                         psycopg.OperationalError(), self.new]) as connect, \
             patch.object(self.db.time, "sleep") as sleep:
            self.assertIs(self.db._connect(), self.new)
        self.assertEqual(connect.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])


if __name__ == "__main__":
    unittest.main()