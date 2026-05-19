# Python TypeError from None Value in Data Processing Pipeline

> case_id: `seed-python-type-error` | severity: **medium** | status: **fixed**

## Environment
- language: Python 3.11
- framework: FastAPI 0.104 / SQLAlchemy 2.0
- runtime: CPython 3.11.4

## Symptoms
A data processing API endpoint crashes with `TypeError` when processing records with missing optional fields. The error occurs when the code tries to perform string operations on a `None` value. The API returns 500 instead of handling the missing data gracefully.

## Error Log
```
TypeError: unsupported operand type(s) for +=: 'NoneType' and 'str'
    at process_record(records/processor.py:89)
    at handle_batch(records/api.py:34)
    at FastAPI.__call__(fastapi/applications.py:276)

Traceback (most recent call last):
  File "/app/services/processor.py", line 89, in process_record
    full_address = record.street + ", " + record.city
                  ~~~~~~~~~~~~~~^~~~~~~~~~~~~~~~~~~~
TypeError: unsupported operand type(s) for +=: 'NoneType' and 'str'
```

## Root Cause
The `process_record()` function concatenates address fields using the `+` operator: `record.street + ", " + record.city`. Some records have `street=None` when the field was not provided during data import. The `+` operator does not handle `None` values in Python — it raises `TypeError` when one operand is `NoneType` and the other is `str`. The code lacks null/None validation before string concatenation.

## Diagnosis Steps
1. Reproduced with a record where `street` field is null in the database
2. Traced error to `processor.py:89` — string concatenation on potentially None values
3. Checked database schema — `street` column is nullable
4. Verified data — 15% of records have null `street` field
5. Confirmed: no None check before string concatenation

## Fix Suggestion
1. Use f-strings with None handling: `f"{record.street or ''}, {record.city or ''}"`
2. Or use `filter(None, [record.street, record.city])` to skip None values
3. Add data validation at the Pydantic model level with default values
4. Write unit tests with records containing optional None fields
5. Consider using `str(record.street or '')` for explicit None-to-empty-string conversion

## Tags
typeerror, python, nonetype, string-concatenation, null-handling, fastapi, data-validation

---
- created: 2025-05-18T11:30:00+00:00
- updated: 2025-05-18T12:00:00+00:00
- similar_cases: []
