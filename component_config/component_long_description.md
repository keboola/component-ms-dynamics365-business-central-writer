The Dynamics 365 Business Central Writer enables you to write data from Keboola Storage to your Microsoft Dynamics 365 Business Central instance.

**Key Features:**
- **OAuth 2.0 Authentication**: Secure authentication with automatic refresh token rotation
- **Create & Update Modes**: Insert new records or update existing ones by key field
- **Dynamic Schema Discovery**: Automatically discover available endpoints and writable fields from your Business Central instance via OData metadata
- **Flexible Field Mapping**: Map input table columns to Business Central fields, including nested fields via dot notation (e.g. `address.city`)
- **Auto Field Mapping**: Use "Re-load Mapping" to automatically match input columns to API fields with fuzzy name matching
- **Error Handling**: Detailed per-record error reporting with optional partial success support — failed records are written to `failed_records.csv`

Perfect for syncing master data, updating financial records, or pushing processed data back into Business Central from your Keboola pipelines.
