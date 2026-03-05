import unittest
from unittest.mock import MagicMock

from keboola.component.exceptions import UserException

from configuration import Configuration, Connection, FieldMapping, WriteMode
from dynamics_client import DynamicsClientError
from writer import BusinessCentralWriter, WriteResult, _unflatten_record


class TestConfiguration(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = {
            "connection": {"tenant_id": "my-tenant"},
            "endpoint": "customers",
            "write_mode": "create",
            "field_mapping": [{"source_column": "Name", "destination_field": "displayName"}],
        }
        defaults.update(kwargs)
        return Configuration(**defaults)

    def test_valid_create_config(self):
        cfg = self._make_config()
        cfg.validate_for_run()  # should not raise

    def test_valid_update_config(self):
        cfg = self._make_config(write_mode="update", key_field="id")
        cfg.validate_for_run()  # should not raise

    def test_missing_tenant_id_raises(self):
        cfg = Configuration(
            **{
                "connection": {"tenant_id": ""},
                "endpoint": "customers",
                "field_mapping": [{"source_column": "a", "destination_field": "b"}],
            }
        )
        with self.assertRaises(UserException):
            cfg.validate_for_run()

    def test_missing_endpoint_raises(self):
        cfg = self._make_config(endpoint="")
        with self.assertRaises(UserException):
            cfg.validate_for_run()

    def test_missing_field_mapping_raises(self):
        cfg = self._make_config(field_mapping=[])
        with self.assertRaises(UserException):
            cfg.validate_for_run()

    def test_update_mode_without_key_field_raises(self):
        cfg = self._make_config(write_mode="update", key_field="")
        with self.assertRaises(UserException):
            cfg.validate_for_run()

    def test_connection_required(self):
        with self.assertRaises(UserException):
            Configuration(**{"endpoint": "customers"})


class TestUnflattenRecord(unittest.TestCase):
    def test_flat_record_unchanged(self):
        record = {"name": "Alice", "age": "30"}
        self.assertEqual(_unflatten_record(record), {"name": "Alice", "age": "30"})

    def test_dot_notation_creates_nested(self):
        record = {"address.city": "Prague", "address.country": "CZ"}
        result = _unflatten_record(record)
        self.assertEqual(result, {"address": {"city": "Prague", "country": "CZ"}})

    def test_deep_nesting(self):
        record = {"a.b.c": "value"}
        result = _unflatten_record(record)
        self.assertEqual(result, {"a": {"b": {"c": "value"}}})

    def test_empty_record(self):
        self.assertEqual(_unflatten_record({}), {})

    def test_mixed_flat_and_nested(self):
        record = {"name": "Alice", "address.city": "Prague"}
        result = _unflatten_record(record)
        self.assertEqual(result, {"name": "Alice", "address": {"city": "Prague"}})


class TestBusinessCentralWriter(unittest.TestCase):
    def _make_writer(self, write_mode=WriteMode.create, key_field="id", continue_on_error=False):
        client = MagicMock()
        return BusinessCentralWriter(
            client=client,
            endpoint="customers",
            write_mode=write_mode,
            key_field=key_field,
            continue_on_error=continue_on_error,
        ), client

    def test_empty_records_returns_empty_result(self):
        writer, _ = self._make_writer()
        result = writer.write_records([])
        self.assertEqual(result.total_processed, 0)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.failed, 0)

    def test_create_success(self):
        writer, client = self._make_writer()
        client.create_record.return_value = {"id": "abc"}
        result = writer.write_records([{"displayName": "Alice"}])
        self.assertEqual(result.total_processed, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.failed, 0)

    def test_create_failure_stops_by_default(self):
        writer, client = self._make_writer()
        client.create_record.side_effect = DynamicsClientError("bad request")
        result = writer.write_records([{"displayName": "Alice"}, {"displayName": "Bob"}])
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.total_processed, 1)

    def test_create_failure_continues_with_flag(self):
        writer, client = self._make_writer(continue_on_error=True)
        client.create_record.side_effect = DynamicsClientError("bad request")
        result = writer.write_records([{"displayName": "Alice"}, {"displayName": "Bob"}])
        self.assertEqual(result.failed, 2)
        self.assertEqual(result.total_processed, 2)

    def test_update_success(self):
        writer, client = self._make_writer(write_mode=WriteMode.update)
        client.update_record.return_value = {}
        result = writer.write_records([{"id": "123", "displayName": "Alice"}])
        self.assertEqual(result.total_processed, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.failed, 0)
        client.update_record.assert_called_once_with("customers", "123", {"displayName": "Alice"})

    def test_update_missing_key_field(self):
        writer, client = self._make_writer(write_mode=WriteMode.update, continue_on_error=True)
        result = writer.write_records([{"displayName": "Alice"}])  # no 'id' key
        self.assertEqual(result.failed, 1)
        self.assertIn("Missing key field", result.errors[0]["error"])

    def test_errors_recorded(self):
        writer, client = self._make_writer(continue_on_error=True)
        client.create_record.side_effect = DynamicsClientError("Invalid field")
        result = writer.write_records([{"displayName": "Alice"}])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Invalid field", result.errors[0]["error"])
        self.assertEqual(result.errors[0]["record"], {"displayName": "Alice"})


if __name__ == "__main__":
    unittest.main()
