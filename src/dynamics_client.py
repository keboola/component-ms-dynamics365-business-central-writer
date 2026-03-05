import logging
import re
import time
from typing import Any
from xml.etree import ElementTree

import requests
from requests import Response
from requests.exceptions import RequestException

from configuration import Configuration

# API Configuration
API_VERSION = "v2.0"

BASE_URL = "https://api.businesscentral.dynamics.com"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

OAUTH_SCOPE = "https://api.businesscentral.dynamics.com/.default offline_access"
DEFAULT_TIMEOUT = 60

# OData XML namespaces for metadata parsing
ODATA_NS = {
    "edmx": "http://docs.oasis-open.org/odata/ns/edmx",
    "edm": "http://docs.oasis-open.org/odata/ns/edm",
}

# Types that are not writable (binary streams, navigation properties)
NON_WRITABLE_TYPES = {"Edm.Stream"}

MAX_RATE_LIMIT_RETRIES = 3


class DynamicsClientError(Exception):
    """Base exception for Dynamics 365 Business Central API errors."""


class DynamicsAuthenticationError(DynamicsClientError):
    """Authentication or authorization failure."""


class DynamicsRateLimitError(DynamicsClientError):
    """API throttling/rate limit error."""


class DynamicsClient:
    """
    Business Central API client for write operations.

    Handles:
    - OAuth token management with automatic refresh
    - OData metadata parsing for schema discovery
    - Record creation and updating via REST API
    """

    def __init__(
        self,
        configuration: Configuration,
        oauth_credentials,
        state: dict[str, Any] | None = None,
    ) -> None:
        self.config = configuration
        self.app_key = oauth_credentials.appKey
        self.app_secret = oauth_credentials.appSecret

        # Extract OAuth tokens from state or credentials
        tokens_from_state = (state or {}).get("oauth", {})
        oauth_data = getattr(oauth_credentials, "data", {}) or {}
        self.access_token: str | None = tokens_from_state.get("#access_token") or oauth_data.get("access_token")
        self.refresh_token: str | None = tokens_from_state.get("#refresh_token") or oauth_data.get("refresh_token")

        self.session = requests.Session()
        self._metadata_cache: dict[str, Any] | None = None
        self._tokens_changed = False

    @property
    def tokens_changed(self) -> bool:
        """Returns True if OAuth tokens were refreshed during this session."""
        return self._tokens_changed

    @property
    def oauth_state(self) -> dict[str, str | None]:
        """Returns current OAuth tokens for state persistence."""
        return {"#access_token": self.access_token, "#refresh_token": self.refresh_token}

    def list_environments(self) -> list[dict[str, Any]]:
        """Fetch available environments from the Business Central instance."""
        url = "https://api.businesscentral.dynamics.com/environments/v1.1"
        response = self._request("GET", url)
        data = response.json()
        return data.get("value", [])

    def list_companies(self) -> list[dict[str, Any]]:
        """Fetch available companies from the Business Central instance."""
        url = self._build_url("companies")
        response = self._request("GET", url, params={"$select": "id,name,displayName"})
        return response.json().get("value", [])

    def list_endpoints(self) -> list[dict[str, str]]:
        """Fetch available API endpoints with human-readable labels."""
        metadata = self._get_metadata()
        entity_sets = metadata.get("entity_sets", {})

        endpoints = [
            {
                "name": name,
                "label": info.get("label") or _humanize_label(name),
                "entity_type": info.get("entity_type"),
            }
            for name, info in entity_sets.items()
        ]
        return sorted(endpoints, key=lambda e: e["label"].lower())

    def list_columns(self, endpoint: str) -> list[dict[str, str]]:
        """Fetch column schema for a specific endpoint."""
        metadata = self._get_metadata()
        entity = metadata.get("entity_sets", {}).get(endpoint)

        if not entity:
            raise DynamicsClientError(f"Endpoint '{endpoint}' not found in metadata.")

        properties = entity.get("properties", [])
        keys = set(entity.get("keys", []))

        columns = [
            {
                "name": prop["name"],
                "type": prop.get("type", ""),
                "label": _humanize_label(prop["name"]),
                "is_key": prop["name"] in keys,
            }
            for prop in properties
            if prop.get("name")
        ]
        return sorted(columns, key=lambda c: c["name"].lower())

    def get_writable_fields(self, endpoint: str) -> list[dict]:
        """Return fields suitable for writing (excluding streams and navigation properties)."""
        columns = self.list_columns(endpoint)
        result = []
        for col in columns:
            col_type = col.get("type", "")
            # Skip binary streams and navigation properties (no type or Collection type)
            if col_type in NON_WRITABLE_TYPES:
                continue
            if col_type.startswith("Collection("):
                continue
            result.append({"field_name": col["name"], "label": col["label"]})
        return result

    def create_record(self, endpoint: str, data: dict) -> dict:
        """POST a new record to the given endpoint."""
        url = self._build_url(endpoint, include_company_scope=True)
        response = self._request("POST", url, json=data)
        return response.json()

    def update_record(self, endpoint: str, record_id: str, data: dict) -> dict:
        """PATCH an existing record identified by record_id."""
        url = self._build_url(f"{endpoint}({record_id})", include_company_scope=True)
        response = self._request("PATCH", url, json=data, headers={"If-Match": "*"})
        # PATCH may return 200 with body or 204 No Content
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def _build_url(
        self, endpoint: str, include_company_scope: bool = False, custom_url_suffix: str | None = None
    ) -> str:
        """Build complete API URL for an endpoint."""
        tenant_id = self.config.connection.tenant_id
        environment = self.config.connection.environment
        company_id = self.config.connection.company_id

        endpoint = endpoint.lstrip("/")

        # Replace placeholders
        for placeholder in ("{companyId}", "{company_id}"):
            endpoint = endpoint.replace(placeholder, company_id)

        # Remove any existing company scope prefix
        if endpoint.lower().startswith("companies("):
            match = re.match(r"companies\([^)]+\)/(.+)", endpoint, re.IGNORECASE)
            if match:
                endpoint = match.group(1)

        parts = [BASE_URL, API_VERSION, tenant_id]
        if environment:
            parts.append(environment)
        parts.extend(["api", API_VERSION])

        if include_company_scope:
            parts.append(f"companies({company_id})")

        parts.append(endpoint)

        if custom_url_suffix:
            custom_url_suffix = custom_url_suffix.lstrip("/")
            parts.append(custom_url_suffix)

        return "/".join(parts)

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
        retry: bool = True,
    ) -> Response:
        """Execute an HTTP request with automatic OAuth refresh on 401 and 429 retry."""
        auth_headers = self._build_auth_headers()
        if headers:
            auth_headers.update(headers)

        logging.debug("%s %s params=%s", method, url, params)

        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=auth_headers,
                    timeout=DEFAULT_TIMEOUT,
                )
            except RequestException as exc:
                raise DynamicsClientError(f"Request failed: {exc}") from exc

            # Handle 401 by refreshing token and retrying once
            if response.status_code == 401 and retry:
                logging.info("Access token expired, refreshing...")
                self._refresh_oauth_token()
                return self._request(method, url, params=params, json=json, headers=headers, retry=False)

            # Handle rate limiting with Retry-After sleep
            if response.status_code == 429:
                if attempt < MAX_RATE_LIMIT_RETRIES - 1:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logging.warning("Rate limited (429). Retrying after %s seconds...", retry_after)
                    time.sleep(retry_after)
                    continue
                raise DynamicsRateLimitError("API throttled the request (HTTP 429). Max retries exceeded.")

            # Handle other errors
            if response.status_code >= 400:
                self._raise_for_status(response, url)

            return response

        raise DynamicsRateLimitError("API throttled the request (HTTP 429). Max retries exceeded.")

    def _build_auth_headers(self) -> dict[str, str]:
        """Build HTTP headers with OAuth bearer token."""
        if not self.access_token:
            raise DynamicsAuthenticationError("Missing OAuth access token.")

        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _refresh_oauth_token(self) -> None:
        """Refresh the OAuth access token using the refresh token."""
        if not self.refresh_token:
            raise DynamicsAuthenticationError("Refresh token not available.")

        payload = {
            "client_id": self.app_key,
            "client_secret": self.app_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": OAUTH_SCOPE,
        }

        try:
            response = self.session.post(TOKEN_URL, data=payload, timeout=DEFAULT_TIMEOUT)
        except RequestException as exc:
            raise DynamicsAuthenticationError(f"Token refresh request failed: {exc}") from exc

        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_msg = error_data.get("error_description") or error_data.get("error") or response.text
            except ValueError:
                error_msg = response.text
            raise DynamicsAuthenticationError(f"Token refresh failed ({response.status_code}): {error_msg}")

        try:
            data = response.json()
        except ValueError as exc:
            raise DynamicsAuthenticationError("Invalid JSON in token refresh response.") from exc

        new_access_token = data.get("access_token")
        if not new_access_token:
            raise DynamicsAuthenticationError("Token refresh response missing access_token.")

        tokens_updated = False
        if new_access_token != self.access_token:
            self.access_token = new_access_token
            tokens_updated = True

        new_refresh_token = data.get("refresh_token")
        if new_refresh_token and new_refresh_token != self.refresh_token:
            self.refresh_token = new_refresh_token
            tokens_updated = True

        self._tokens_changed = self._tokens_changed or tokens_updated

    def _raise_for_status(self, response: Response, url: str) -> None:
        """Parse API error response and raise appropriate exception."""
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        message = (
            payload.get("error", {}).get("message")
            or payload.get("message")
            or response.text
            or f"HTTP {response.status_code}"
        )

        if response.status_code in (401, 403):
            raise DynamicsAuthenticationError(f"Authentication failed: {message}")

        if response.status_code == 400:
            raise DynamicsClientError(f"Invalid request: {message}")

        raise DynamicsClientError(f"API error {response.status_code} at {url}: {message}")

    def _get_metadata(self) -> dict[str, Any]:
        """Fetch and cache the OData metadata document."""
        if self._metadata_cache is not None:
            return self._metadata_cache

        metadata_url = self._build_url("$metadata")
        response = self._request("GET", metadata_url)
        self._metadata_cache = _parse_odata_metadata(response.text)
        return self._metadata_cache


def _parse_odata_metadata(xml_body: str) -> dict[str, Any]:
    """Parse OData $metadata XML into a structured dictionary."""
    try:
        root = ElementTree.fromstring(xml_body)
    except ElementTree.ParseError as exc:
        raise DynamicsClientError(f"Failed to parse metadata XML: {exc}") from exc

    entity_types: dict[str, dict[str, Any]] = {}
    entity_sets: dict[str, dict[str, Any]] = {}

    schemas = root.findall("edmx:DataServices/edm:Schema", namespaces=ODATA_NS)
    for schema in schemas:
        namespace = schema.attrib.get("Namespace", "")

        for entity_type in schema.findall("edm:EntityType", namespaces=ODATA_NS):
            name = entity_type.attrib.get("Name")
            full_name = f"{namespace}.{name}" if namespace else name
            if not full_name:
                continue

            properties = [
                {"name": prop.attrib.get("Name"), "type": prop.attrib.get("Type")}
                for prop in entity_type.findall("edm:Property", namespaces=ODATA_NS)
            ]

            keys = [
                key.attrib.get("Name")
                for key in entity_type.findall("edm:Key/edm:PropertyRef", namespaces=ODATA_NS)
                if key.attrib.get("Name")
            ]

            entity_types[full_name] = {"properties": properties, "keys": keys}

        for container in schema.findall("edm:EntityContainer", namespaces=ODATA_NS):
            for entity_set in container.findall("edm:EntitySet", namespaces=ODATA_NS):
                name = entity_set.attrib.get("Name")
                entity_type_name = entity_set.attrib.get("EntityType")

                if not name or not entity_type_name:
                    continue

                entity_info = entity_types.get(entity_type_name, {})
                entity_sets[name] = {
                    "entity_type": entity_type_name,
                    "properties": entity_info.get("properties", []),
                    "keys": entity_info.get("keys", []),
                    "label": _humanize_label(name),
                }

    return {"entity_sets": entity_sets, "entity_types": entity_types}


def _humanize_label(value: str) -> str:
    """Convert camelCase or snake_case to a human-readable label."""
    if not value:
        return value

    spaced = re.sub(r"(?<!^)(?<![A-Z])(?=[A-Z])", " ", value)
    label = spaced.replace("_", " ").strip()

    return label.capitalize()
