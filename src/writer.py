import logging
import time
from dataclasses import dataclass, field

from keboola.component.exceptions import UserException

from configuration import WriteMode
from dynamics_client import DynamicsClient, DynamicsClientError

# Conservative rate limit: 500 requests/min = 0.12s between requests
_RATE_LIMIT_SLEEP = 60.0 / 500


@dataclass
class WriteResult:
    total_processed: int = 0
    created: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)


class BusinessCentralWriter:
    def __init__(
        self,
        client: DynamicsClient,
        endpoint: str,
        write_mode: WriteMode,
        key_field: str = "",
        continue_on_error: bool = False,
    ):
        self.client = client
        self.endpoint = endpoint
        self.write_mode = write_mode
        self.key_field = key_field
        self.continue_on_error = continue_on_error

    def write_records(self, records: list[dict]) -> WriteResult:
        if not records:
            return WriteResult()

        result = WriteResult()

        for i, record in enumerate(records):
            result.total_processed += 1

            try:
                if self.write_mode == WriteMode.create:
                    self._create_one(record, result)
                else:
                    self._update_one(record, result)
            except UserException as e:
                # Fatal errors (auth failure, network error) — stop immediately
                logging.error("Fatal error on record %d, aborting: %s", i + 1, e)
                remaining = records[i + 1 :]
                result.total_processed += len(remaining)
                result.failed += len(remaining)
                result.errors.extend({"record": r, "error": str(e)} for r in remaining)
                break

            # Stop on first failure unless continue_on_error is set
            if result.failed > 0 and not self.continue_on_error:
                remaining = records[i + 1 :]
                if remaining:
                    logging.info("Stopping early due to error. %d records not processed.", len(remaining))
                break

            time.sleep(_RATE_LIMIT_SLEEP)

        return result

    def _create_one(self, record: dict, result: WriteResult) -> None:
        data = _unflatten_record(record)
        try:
            self.client.create_record(self.endpoint, data)
            result.created += 1
        except DynamicsClientError as e:
            result.failed += 1
            result.errors.append({"record": record, "error": str(e)})
            logging.warning("Failed to create record: %s", e)

    def _update_one(self, record: dict, result: WriteResult) -> None:
        key_value = record.get(self.key_field)

        if not key_value:
            result.failed += 1
            result.errors.append({"record": record, "error": f"Missing key field '{self.key_field}'"})
            logging.warning("Skipping record with missing key field '%s'", self.key_field)
            return

        data = {k: v for k, v in record.items() if k != self.key_field}
        unflattened = _unflatten_record(data)

        try:
            self.client.update_record(self.endpoint, key_value, unflattened)
            result.updated += 1
        except DynamicsClientError as e:
            result.failed += 1
            result.errors.append({"record": record, "error": str(e)})
            logging.warning("Failed to update record '%s': %s", key_value, e)


def _unflatten_record(record: dict) -> dict:
    """Convert dot-notation keys (e.g. 'address.city') into nested dicts."""
    result = {}

    for key, value in record.items():
        parts = key.split(".")
        current = result

        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]

        current[parts[-1]] = value

    return result
