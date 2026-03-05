import csv
import logging
from typing import Any

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import SelectElement

from configuration import Configuration
from dynamics_client import (
    DynamicsAuthenticationError,
    DynamicsClient,
    DynamicsClientError,
    DynamicsRateLimitError,
)
from writer import BusinessCentralWriter


class Component(ComponentBase):
    def __init__(self) -> None:
        super().__init__()

        # Suppress info logging for sync actions
        action = self.configuration.action
        if action and action != "run":
            logging.getLogger().setLevel(logging.CRITICAL)

        self.config: Configuration = Configuration(**self.configuration.parameters)
        self.state: dict[str, Any] = self._load_state()
        self.client: DynamicsClient = DynamicsClient(self.config, self.configuration.oauth_credentials, self.state)

    def run(self) -> None:
        self.config.validate_for_run()

        # Get first input table
        input_tables = self.get_input_tables_definitions()
        if not input_tables:
            raise UserException("No input table found. Please map an input table in the configuration.")
        input_table = input_tables[0]

        # Build column mapping from field_mapping config
        column_mapping = {fm.source_column: fm.destination_field for fm in self.config.field_mapping}

        # Read CSV, validate columns, apply mapping
        records = []
        with open(input_table.full_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            csv_columns = reader.fieldnames or []

            # Validate source columns exist
            cols_to_check = list(column_mapping.keys())
            if self.config.key_field:
                cols_to_check.append(self.config.key_field)
            missing = [col for col in cols_to_check if col not in csv_columns]
            if missing:
                raise UserException(
                    f"Source columns not found in input table: {', '.join(missing)}. "
                    f"Available columns: {', '.join(csv_columns)}"
                )

            for row in reader:
                mapped: dict[str, Any] = {}
                for source_col, dest_field in column_mapping.items():
                    mapped[dest_field] = row[source_col]
                # Include key field in mapped record for update mode
                if self.config.key_field:
                    mapped[self.config.key_field] = row[self.config.key_field]
                records.append(mapped)

        if not records:
            logging.warning("Input table is empty, nothing to write.")
            self._sync_tokens_if_needed()
            self.write_state_file(self.state)
            return

        logging.info("Read %d records from input table '%s'", len(records), input_table.name)

        # Write records to Business Central
        try:
            writer = BusinessCentralWriter(
                client=self.client,
                endpoint=self.config.endpoint,
                write_mode=self.config.write_mode,
                key_field=self.config.key_field,
                continue_on_error=self.config.continue_on_error,
            )
            result = writer.write_records(records)
        except DynamicsClientError as e:
            raise self._wrap_client_error(e)

        # Log summary
        logging.info(
            "Write complete. Total: %d, Created: %d, Updated: %d, Failed: %d",
            result.total_processed,
            result.created,
            result.updated,
            result.failed,
        )

        if result.errors:
            for error in result.errors[:10]:
                logging.warning("Error: %s", error["error"])
            if len(result.errors) > 10:
                logging.warning("... and %d more errors", len(result.errors) - 10)

        if result.failed > 0:
            if self.config.continue_on_error:
                self._write_failed_records(result.errors, column_mapping)
                logging.warning(
                    "%d out of %d records failed. Failed records written to 'failed_records.csv'.",
                    result.failed,
                    result.total_processed,
                )
            else:
                first_error = result.errors[0]["error"] if result.errors else "Unknown error"
                raise UserException(
                    f"{result.failed} out of {result.total_processed} records failed to write. "
                    f"First error: {first_error}"
                )

        self._sync_tokens_if_needed()
        self.write_state_file(self.state)

    def _load_state(self) -> dict[str, Any]:
        state = self.get_state_file()
        return state if isinstance(state, dict) else {}

    def _sync_tokens_if_needed(self) -> None:
        if self.client.tokens_changed:
            self.state["oauth"] = self.client.oauth_state

    def _write_failed_records(self, errors: list[dict], column_mapping: dict[str, str]) -> None:
        """Write failed records with error details to an output table."""
        dest_fields = list(column_mapping.values())
        if self.config.key_field and self.config.key_field not in dest_fields:
            dest_fields.append(self.config.key_field)
        fieldnames = dest_fields + ["error_message"]

        out_table = self.create_out_table_definition("failed_records.csv")
        with open(out_table.full_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for entry in errors:
                row = {field: entry["record"].get(field, "") for field in dest_fields}
                row["error_message"] = entry["error"]
                writer.writerow(row)

        self.write_manifest(out_table)
        logging.info("Wrote %d failed records to 'failed_records.csv'", len(errors))

    @sync_action("testConnection")
    def test_connection(self):
        """Test API connectivity by fetching the list of companies."""
        try:
            self.client.list_companies()
        except DynamicsClientError as exc:
            raise self._wrap_client_error(exc)

    @sync_action("list_environments")
    def list_environments(self):
        """Fetch available environments for UI dropdown."""
        try:
            environments = self.client.list_environments()
        except DynamicsClientError as exc:
            raise self._wrap_client_error(exc)

        return [SelectElement(item.get("name", "")) for item in environments if item.get("name")]

    @sync_action("list_companies")
    def list_companies(self):
        """Fetch available companies for UI dropdown."""
        try:
            companies = self.client.list_companies()
        except DynamicsClientError as exc:
            raise self._wrap_client_error(exc)

        return [SelectElement(value=item["id"], label=item.get("name") or item["id"]) for item in companies]

    @sync_action("list_endpoints")
    def list_endpoints(self):
        """Fetch available API endpoints for UI dropdown."""
        try:
            endpoints = self.client.list_endpoints()
        except DynamicsClientError as exc:
            raise self._wrap_client_error(exc)

        return [SelectElement(value=item["name"], label=item.get("label") or item["name"]) for item in endpoints]

    @sync_action("load_entity_fields")
    def load_entity_fields(self):
        """Load writable fields for the selected endpoint (used for field_mapping and key_field dropdowns)."""
        endpoint = self.configuration.parameters.get("endpoint")
        if not endpoint:
            return {"type": "data", "data": {"api_fields": []}}

        try:
            api_fields = self.client.get_writable_fields(endpoint)
        except DynamicsClientError as exc:
            raise self._wrap_client_error(exc)

        self._sync_tokens_if_needed()
        return {"type": "data", "data": {"api_fields": api_fields}}

    @staticmethod
    def _wrap_client_error(error: DynamicsClientError) -> UserException:
        """Convert API client errors to user-friendly messages."""
        if isinstance(error, DynamicsAuthenticationError):
            message = f"Authentication failed: {error}"
        elif isinstance(error, DynamicsRateLimitError):
            message = "Dynamics 365 Business Central throttled the request. Consider scheduling runs less frequently."
        else:
            message = str(error)

        return UserException(message)


if __name__ == "__main__":
    try:
        comp = Component()
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
