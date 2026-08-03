---
name: tabular-field-discovery
description: Tabular field and schema discovery skill. Use when the user uploads or references structured tabular files (markdown tables, CSV, Excel exports, plain-text grids) and wants to identify, map, or align similar columns/fields across one or more files — even when column names differ slightly. Best for merge/join prep, schema comparison, and data extraction. For downstream analysis or visualization use the appropriate data or chart skill after field discovery is complete.
license: MIT license
metadata:
    skill-author: internal
risk: unknown
source: community
---

# Tabular Field Discovery

Identify, classify, and align fields (columns) across one or more structured tabular files, even when headers are inconsistently named, partially missing, or embedded in messy markdown/text output.

## When to Use
- One or more uploaded files contain rows of records (students, products, transactions, etc.) and you need to understand their structure.
- Column headers may be worded differently across files (e.g. "Roll no" vs "Roll Number" vs "ID").
- The user wants to merge, join, or cross-reference multiple tabular files and needs a confirmed field map first.
- The table was converted from PDF/image and may have garbled or merged header cells.
- The user asks things like "find matching fields", "what columns are common", "map these tables", "compare schemas", "align fields", or "extract column structure".

## Quick Start

Read the file(s), detect headers, classify each field, align across files, and output a canonical schema. Always confirm the schema before writing any merge or analysis code.

Example — for three exam result files:
```
Canonical Schema (snake_case):
  serial_no       → rank        → "S No" in all files
  roll_no         → identifier  → "Roll no" in all files
  student_name    → name        → "Student Name" in all files
  maths_marks     → score       → "Maths Marks" in all files
  physics_marks   → score       → "Physics Marks" / split header in WTM-31 ⚠️
  chemistry_marks → score       → "Chemistry Marks" in all files
  total_marks     → aggregate   → "Total Marks" in all files
```

## Choosing the Right Reading Strategy

### File Already in Context
If the file content is visible as a `<document>` block — read it directly. Do NOT re-read from disk.

### File Only Listed by Path
If only the path appears (e.g. `/mnt/user-data/uploads/...`), use the `view` tool to read it.

### Excel Files
Use the xlsx skill to extract content before running field discovery.

## Core Steps

### Step 1 — Detect the Table Region

Find the header row in each file:
- **Markdown:** the row immediately before the `|---|---|` separator line.
- **CSV:** typically the first non-empty row.
- **Plain text:** the row where all cells are short labels, not numeric data.

If a file has multiple tables, detect each one separately and label them (Table A, Table B, …).

### Step 2 — Extract Raw Headers

List every column header exactly as it appears, preserving spacing and casing:

```
Raw headers — WTM-29:
  Col 1 : "S No"
  Col 2 : "Roll no"
  Col 3 : "Student Name"
  Col 4 : "Maths Marks"
  Col 5 : "Physics Marks"
  Col 6 : "Chemistry Marks"
  Col 7 : "Total Marks"
```

Flag any header that is:
- Empty or whitespace-only → label `[UNNAMED_COL_N]`
- Merged or split across two rows → reconstruct and note it
- Duplicated → note each occurrence

### Step 3 — Classify Each Field

Assign a **semantic type** to every column:

| Semantic Type  | Description                                          | Typical Examples                    |
|----------------|------------------------------------------------------|-------------------------------------|
| `identifier`   | Unique record ID (numeric or alphanumeric)           | Roll No, Student ID, Employee Code  |
| `name`         | Human name or label for the record                   | Student Name, Candidate, Author     |
| `score`        | Numeric score, marks, or points for a subject        | Maths Marks, Physics Score          |
| `aggregate`    | Sum or computed total across scores                  | Total Marks, Grand Total            |
| `rank`         | Position within a group                              | Rank, S No, Position                |
| `date`         | A date or timestamp                                  | Exam Date, DOB, Test Date           |
| `category`     | A grouping or classification label                   | Batch, Section, Gender, Stream      |
| `percentage`   | A ratio expressed as percent                         | Pass %, Accuracy, Percentile        |
| `boolean_flag` | Yes/No, Pass/Fail, Present/Absent                    | Absent, Promoted, Cleared           |
| `free_text`    | Unstructured text                                    | Remarks, Comments, Address          |
| `unknown`      | Cannot be determined from headers or sample values   | —                                   |

Also note for each column:
- **Nullable** — whether blank/missing values appear
- **Signed** — whether negative values appear (e.g. penalty marking)
- **Observed range** — min and max values seen in the data

### Step 4 — Cross-File Alignment

When two or more files are provided, produce an **alignment table**:

```
Logical Field       | WTM-29 Col    | WTM-30 Col    | WTM-31 Col       | Type
--------------------|---------------|---------------|------------------|------------
Serial Number       | S No          | S No          | S No             | rank
Student ID          | Roll no       | Roll no       | Roll no          | identifier
Student Name        | Student Name  | Student Name  | Student Name     | name
Maths Score         | Maths Marks   | Maths Marks   | Maths Marks      | score
Physics Score       | Physics Marks | Physics Marks | Physics (split)  | score ⚠️
Chemistry Score     | Chemistry Marks| Chemistry Marks| Chemistry Marks | score
Total               | Total Marks   | Total Marks   | Total Marks      | aggregate
```

Matching rules (apply in order):
1. **Exact match** — identical header string → definite match
2. **Normalized match** — lowercase + strip punctuation/spaces → e.g. "Roll no" ≈ "rollno"
3. **Semantic match** — different wording, same meaning → flag as *probable match, confirm*
4. **New column** — present in one file but not another → mark `[ONLY IN FILE X]`
5. **Dropped column** — present earlier but absent later → mark `[DROPPED IN FILE Y]`

### Step 5 — Canonical Schema Output

Produce a clean schema with snake_case names ready for downstream code:

```
Canonical Schema:
  serial_no       → rank        → S No (all files)
  roll_no         → identifier  → Roll no (all files)
  student_name    → name        → Student Name (all files)
  maths_marks     → score       → Maths Marks (all files)  [range: -2 to 71]
  physics_marks   → score       → Physics Marks (all files) ⚠️ split header in WTM-31
  chemistry_marks → score       → Chemistry Marks (all files) [negatives observed]
  total_marks     → aggregate   → Total Marks (all files)  [range: -12 to 151]
```

## Common Edge Cases

| Problem | How to Handle |
|---------|---------------|
| Header row split across two markdown rows | Concatenate with a space; note in output |
| Roll numbers with leading zeros | Treat as string `identifier`; warn about numeric parsing |
| Negative marks (penalty scoring) | Flag `signed: true` on score columns |
| Students absent from some tests | Note as `[NOT IN FILE X]`; do not impute values |
| Duplicate roll numbers in same file | Flag as data quality issue |
| Rows with no name (e.g. roll no `7403000`) | Tag as `[UNIDENTIFIED_RECORD]` |
| Two tables concatenated in one file | Treat as separate schemas; align independently |
| Column order changed between files | Always match by name, not position |

## Output Checklist

Before finishing, confirm all of the following are present in your response:

- [ ] Raw headers listed per file, exactly as found
- [ ] Semantic type assigned to every column
- [ ] Alignment table produced (if more than one file)
- [ ] Discrepancies and anomalies flagged with ⚠️
- [ ] Canonical schema with snake_case names
- [ ] Data quality issues noted (negatives, blanks, unidentified records, duplicates)

## Downstream Tasks Enabled

Once the schema is confirmed, proceed to:
- **Merge / join** records across files on `roll_no`
- **Trend analysis** — track `total_marks` per student across test dates
- **Subject breakdown** — compare `maths_marks`, `physics_marks`, `chemistry_marks` over time
- **Rank computation** — sort by `total_marks` and assign rank per test
- **Absentee detection** — find roll numbers missing from one or more files
- **Export** — write merged data to `.xlsx`, `.csv`, or a markdown report

## Additional Resources

- For Excel input files: use the xlsx skill to extract content first
- For PDF-converted tables: use the pdf-reading skill to get clean text before field discovery
- For analysis after schema is confirmed: use the data-analysis skill

## Limitations
- Use this skill only when the task clearly matches the scope described above.
- Do not treat the output as a substitute for environment-specific validation, testing, or expert review.
- Stop and ask for clarification if required inputs, permissions, safety boundaries, or success criteria are missing.
