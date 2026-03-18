Dynamics 365 Business Central Writer
=============

Writes data from Keboola input tables to Microsoft Dynamics 365 Business Central via the REST API.

**Table of Contents:**

[TOC]

Functionality Notes
===================

The component reads an input table, maps columns to Business Central API fields, and writes records using the Business Central OData REST API. It supports creating new records and updating existing records by key field. Available endpoints and fields are discovered dynamically from your Business Central instance via the OData `$metadata` document.

Prerequisites
=============

- A Microsoft Dynamics 365 Business Central account with API access.
- OAuth authorization configured in Keboola (the component uses OAuth2 refresh token flow).
- Your Azure AD **Tenant ID** (found in the Business Central URL: `https://businesscentral.dynamics.com/__TENANT_ID__/Production`).

Features
========

| **Feature**             | **Description**                                              |
|-------------------------|--------------------------------------------------------------|
| Generic UI Form         | Dynamic UI form with endpoint and field auto-loading.        |
| Row-Based Configuration | Each config row targets a separate endpoint.                 |
| OAuth                   | OAuth2 authentication with automatic token refresh.          |
| Dynamic Schema          | Endpoints and writable fields loaded from OData metadata.    |
| Auto Field Mapping      | Fuzzy-matches input columns to API fields automatically.     |
| Per-Record Error Handling | Failed records written to `failed_records.csv` optionally. |

Supported Endpoints
===================

All writable Business Central API endpoints are supported. The list of available endpoints is loaded dynamically from the OData metadata of your Business Central instance. If you need additional endpoints, please submit your request to [ideas.keboola.com](https://ideas.keboola.com/).

Configuration
=============

Global Configuration (config.json)
-----------------------------------

### Connection

**Tenant ID** — Azure AD tenant identifier. Found in the Business Central URL: `https://businesscentral.dynamics.com/__TENANT_ID__/Production`.

**Environment** — Business Central environment name (e.g. `Production`, `Sandbox`). Loaded dynamically via "Load environments".

**Company ID** — GUID of the company to write data to. Loaded dynamically via "Load companies".

Row Configuration (configRow.json)
------------------------------------

### Endpoint

The Business Central API endpoint to write to (e.g. `customers`, `vendors`, `salesOrders`). Loaded dynamically via "Re-load endpoints".

### Write Mode

- **Create** — insert new records via POST.
- **Update** — modify existing records by key via PATCH.

### Key Field

Required for update mode. The Business Central field used as the record identifier (e.g. `id`, `number`). Must also be present as a column in the input table.

### Field Mapping

Maps input table columns to Business Central API fields. Use "Re-load Mapping" to auto-populate the mapping — existing rows are preserved and new columns are fuzzy-matched to API field names. Nested fields are supported via dot notation (e.g. `address.city`).

### Continue on Error

When enabled, records that fail to write are not treated as fatal errors. The component logs the failure and writes the failed records (with error messages) to an output table `failed_records.csv`. The job continues processing remaining records. When disabled (default), any failed record causes the job to fail immediately.

### Debug Mode

Enables verbose debug logging for troubleshooting.

Output
======

On success, the component logs a summary: total records processed, created, updated, and failed counts.

When **Continue on Error** is enabled and some records fail, an output table `failed_records.csv` is produced containing the failed records along with an `error_message` column.

Development
-----------

To customize the local data folder path, replace the `CUSTOM_FOLDER` placeholder with your desired path in the `docker-compose.yml` file:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    volumes:
      - ./:/code
      - ./CUSTOM_FOLDER:/data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone this repository, initialize the workspace, and run the component using the following commands:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
git clone https://github.com/keboola/component-ms-dynamics365-business-central-writer component-dynamics-365-business-central-writer
cd component-dynamics-365-business-central-writer
docker-compose build
docker-compose run --rm dev
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run the test suite and perform lint checks using this command:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
docker-compose run --rm test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration
===========

For details about deployment and integration with Keboola, refer to the
[deployment section of the developer
documentation](https://developers.keboola.com/extend/component/deployment/).
