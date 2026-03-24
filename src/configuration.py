from enum import Enum

from keboola.component.exceptions import UserException
from pydantic import BaseModel, Field, ValidationError


class WriteMode(str, Enum):
    create = "create"
    update = "update"


class FieldMapping(BaseModel):
    source_column: str
    destination_field: str


class Connection(BaseModel):
    tenant_id: str
    environment: str = ""
    company_id: str = ""


class Configuration(BaseModel):
    connection: Connection
    endpoint: str = ""
    write_mode: WriteMode = WriteMode.create
    key_field: str = ""
    field_mapping: list[FieldMapping] = Field(default_factory=list)
    continue_on_error: bool = False
    debug: bool = False

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except ValidationError as e:
            error_messages = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            raise UserException(f"Validation Error: {', '.join(error_messages)}")

    def validate_for_run(self) -> None:
        errors = []
        if not self.connection.tenant_id:
            errors.append("Tenant ID is required")
        if not self.endpoint:
            errors.append("Endpoint is required")
        if not self.field_mapping:
            errors.append("Field mapping is required")
        if self.write_mode == WriteMode.update and not self.key_field:
            errors.append("Key field is required when write mode is 'update'")
        if errors:
            raise UserException(f"Configuration error: {', '.join(errors)}")
