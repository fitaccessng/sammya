# Universal Import Operations

## Database Initialization

This repository does not currently use an Alembic migration directory. Startup initialization creates the shared import tables with `checkfirst` and adds nullable import-related columns using additive `ALTER TABLE` statements. No existing rows or tables are dropped.

New shared tables:

- `import_job`
- `import_job_file`
- `import_template`
- `import_mapping_learning`
- `boq_import`
- `boq_import_sheet`
- `boq_import_row`

Existing domain tables receive nullable additions for supported imported attributes: `vendor.contact_person`, `boq_item.item_no`, `purchase_order_item.expected_delivery_date`, `inventory.sku`, `inventory.unit_cost`, `inventory.warehouse_name`, `milestone.task_code`, and `milestone.assignee_name`. Existing `import_template` and `import_job` tables receive the new template/date preference columns when absent.

Deployments must use a database role with permission to `CREATE TABLE` and `ALTER TABLE` during application initialization. The BOQ archive tables are created individually with `checkfirst`; they do not modify the normalized BOQ schema. If those archive tables cannot be created, structured BOQ viewing and entry remain available, while preserving new uploaded workbook rows requires the tables to be initialized. Back up production data and verify the DDL with the deployment database administrator before rollout. For production environments that disable startup table creation, run the additive initializer with a controlled maintenance command under a role with DDL permission before deploying the new application version.

`project_document.file_data` is a nullable binary column added with an additive `ALTER TABLE` for existing databases. QS BOQ uploads store the original file bytes there so document view/download continues to work after an ephemeral application container is replaced. This increases database storage use; deployments must allow the column DDL and account for uploaded-file database capacity. Previously uploaded files that have already disappeared from ephemeral storage cannot be reconstructed and must be uploaded again.

## Private Import Files

Uploaded source files are staged under `IMPORT_PRIVATE_STORAGE`. If unset, the application uses a private `sammyaerp-imports` directory under the operating-system temporary directory. Files are created with owner-only directory/file permissions and are never served as static assets. Set `IMPORT_PRIVATE_STORAGE` to a persistent, access-controlled directory if users need to change workbook sheets/header rows after a process restart. Preserve the directory only as long as import review requires the source; normalized original cell values and per-file analysis remain in the import-job record.

The application request-size limit remains controlled by `MAX_UPLOAD_SIZE` (50 MiB by default). The import endpoint also caps combined multi-file uploads at 200 MiB, but the Flask request limit may be lower and applies first.

## Finance Authorization Boundary

Bank statement imports require an authenticated user in the existing Finance role allow-list and an active bank account. The application has no company model or bank-account-to-user ACL, so access remains role-based across active accounts. No company-level isolation is implemented or implied.

## Browser Acceptance Tests

Python Playwright is pinned in `requirements.txt`. Install the matching browser runtime once per environment:

```sh
python -m playwright install chromium
```

Run browser tests separately from the fast HTTP/model suite:

```sh
PYTHONPATH=. pytest tests/test_import_browser.py -q
```

Browser tests create a temporary SQLite database and a local HTTP server. They use synthetic Finance/QS fixtures only. Browser binaries are installed in the user's Playwright cache and are not stored in this repository.
