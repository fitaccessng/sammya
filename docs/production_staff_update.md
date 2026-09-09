# Production Staff Excel Update Runbook

Use this flow when HR data must be corrected in production without manually editing rows in the database.

## 1. Export from production

Open the production app as an HR/admin user:

1. Go to HR > Staff Management.
2. Click **Export Excel**.
3. Save the exported file as the working copy.

## 2. Edit and import through the app

For new staff, use HR > Staff Management > Import from Excel. The app will validate the file, create a preview batch, and require admin approval before creating users.

For existing staff corrections, edit the exported Excel and keep `employee_id` unchanged. The controlled script below updates existing users, compensation, deductions, next-of-kin, and department access.

## 3. Run migrations/seeds first

Always run a dry run before apply:

```bash
DATABASE_URL="postgresql://..." python scripts/production_migrate_seed.py --dry-run
DATABASE_URL="postgresql://..." python scripts/production_migrate_seed.py --apply
```

To seed or verify an emergency admin:

```bash
SEED_ADMIN_EMAIL="admin@example.com" SEED_ADMIN_PASSWORD="change-this" \
DATABASE_URL="postgresql://..." python scripts/production_migrate_seed.py --apply --seed-admin
```

## 4. Controlled production data update

Dry run first:

```bash
DATABASE_URL="postgresql://..." python scripts/sync_staff_from_excel.py "./staff_export_updated.xlsx"
```

Apply only after reviewing the counts:

```bash
DATABASE_URL="postgresql://..." python scripts/sync_staff_from_excel.py "./staff_export_updated.xlsx" --apply --confirm-production-update
```

Add `--create-missing` only when the spreadsheet intentionally contains new staff that should be created outside the app approval workflow.

## 5. Verify

After apply, export again from HR > Staff Management and compare the production export with the approved working file.
