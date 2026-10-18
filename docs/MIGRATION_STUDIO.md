# Migration Studio guide

The Onboarding Portal's Migration Studio imports a CSV into a selected tenant
database table. It is an explicit administrative import tool, not a general
database editor. Take a verified tenant backup first and test the mapping on a
small representative CSV before importing production data.

## Import workflow

1. Open **Migration Studio**, select the hospital tenant and destination table,
   then upload a UTF-8 CSV with a header row. The portal displays the CSV
   columns as source nodes and destination fields as target nodes. ETL blocks
   are arranged in a horizontal lane between the source and destination columns;
   additional blocks extend the lane to the right.
2. Drag connections from CSV columns into destination fields. To transform a
   value, add a block, connect the source into it, and connect its output to a
   target field. Blocks can be chained. Delete a block with its trash icon;
   deleting it also removes its connections.
3. Configure blocks from **Configure**. Supported operations include
   constants, trim, upper/lower case, value maps, text replacement, conditions,
   combine, first-non-empty selection, null-if, substring, split-part, number
   parsing/arithmetic, and date parsing. The server validates an allowlisted
   expression tree; arbitrary Python/SQL is not accepted.
4. Choose duplicate behavior and a destination field with a single-column
   unique constraint. The field must also be mapped. **Skip** is the safest
   default; **Reject** leaves matching rows unchanged and counts them as
   rejected; **Update** overwrites the mapped fields on the matching row;
   **Allow** attempts every row and leaves constraint enforcement to the
   database. Update only after reviewing the mapping and confirming the
   backup. Composite unique keys are not currently offered as a duplicate
   selector.
5. Select **Preview Data**. Review transformed sample rows, validation errors,
   and the detected duplicate count. Preview is advisory: data may change
   between preview and execution, and unrelated database constraints can still
   reject rows.
6. Select **Run Migration** and confirm. The API queues the work in an
   in-process background task; keep the API container running until it
   finishes. Counts and a bounded sample of row errors are written to the
   control-plane forensic audit ledger. Row-level migration actions use the
   vendor-console actor in that ledger; tenant `created_by_id` / `updated_by_id`
   remain null because the vendor setup key does not identify an authenticated
   tenant user. Timestamps are maintained by model/database defaults.

## Supported expression blocks

| Block | Configuration / behavior |
| --- | --- |
| Constant | Supplies a fixed scalar value. |
| Trim / Uppercase / Lowercase | Normalizes a single input string. |
| Map values | JSON object maps source values to destination values. |
| Replace text | Replaces every occurrence of a configured substring. |
| Conditional value | Compares one input using equals, not-equals, contains, is-empty, or not-empty, then emits one of two text values. |
| Combine inputs | Joins two or more inputs, skipping empty values, with a configured separator. |
| First non-empty | Emits the first non-empty value from two or more connected inputs, in connection order. |
| Null-if | Converts a configured matching value to null. |
| Substring / Split-part | Extracts a configured slice or zero-based delimited part. |
| Parse number / arithmetic | Converts numeric text and applies a configured add, subtract, multiply, or divide operation. Division by zero is rejected for that row. |
| Parse date | Parses using a Python `strptime` format and emits the configured `strftime` format. Example: `%d/%m/%Y` to `%Y-%m-%d`. |

Expressions have configured depth/input bounds. A migration table's catalog
does not imply that every table is importable: tables with protected required
fields are intentionally blocked, and foreign keys and model-specific
validation still apply. The current invoice flow resolves a mapped patient MRN
to an existing patient record; it does not create missing patients.

## Limitations and operational safety

- The current worker is in-process, not a durable queue. A process/container
  restart can interrupt an active job. Do not deploy large imports during
  planned restarts; persistent job queueing and resumability are future work.
- The preview returns a sample, plus a duplicate count and at most a bounded
  number of validation errors. It does not guarantee every row will commit.
- Duplicate detection supports a single unique destination column only.
  Database constraints remain the final authority, including for races,
  composite uniqueness, and foreign keys.
- **Update** is not a merge strategy: it replaces mapped values on the
  matching row and can overwrite clinically relevant fields. Do not choose it
  unless the data owner approves that behavior.
- Upload size, preview bounds, expression depth, and maximum input counts are
  centrally configurable using `MIGRATION_MAX_UPLOAD_BYTES`,
  `MIGRATION_PREVIEW_ROWS`, `MIGRATION_PREVIEW_ERROR_LIMIT`,
  `MIGRATION_MAX_EXPRESSION_DEPTH`, and `MIGRATION_MAX_INPUTS`.
