# Day 1: Building the Ingestor — FinTrack's Front Door

Every pipeline needs an entry point. Today I built FinTrack's: the **ingestor**, the piece of code responsible for taking a raw bank statement PDF off disk and getting it into the database in a clean, traceable way.

## What the ingestor actually does

The flow is straightforward on paper:

1. Compute a SHA-256 hash of the PDF file
2. Check if that hash already exists in the database — if so, skip silently
3. Extract all text from the PDF using `pdfplumber`
4. Build a `RawDocument` record and persist it

That dedup check (step 2) is more important than it sounds. Bank statements are easy to accidentally re-download, and ingesting the same file twice would pollute every downstream step. The hash makes re-ingestion a no-op, no matter how many times the same file crosses the pipeline.

## Two classes, one file

The ingestor is split into two classes: `DocumentParser`, which handles the PDF-to-text extraction, and `Ingestor`, which owns the database connection and orchestrates everything. Keeping them separate means the parsing logic can be tested without touching a database at all — something that paid off immediately when writing the tests.

```python
def extract_text(self, file_path: str) -> str:
    with pdfplumber.open(file_path) as pdf:
        pages_text = []
        for page in pdf.pages:
            pages_text.append(page.extract_text() or "")
    return "\n".join(pages_text)
```

The `or ""` guard is small but necessary — `pdfplumber` returns `None` for pages it can't read, and joining `None` into a string is the kind of silent bug that only shows up on a weird statement format at 11pm.

## One deliberate design choice

The `RawDocument` that comes out of the ingestor has `parsed = False`. That flag stays `False` until the *normalizer* (Step 2 of the pipeline, not built yet) processes the raw text into structured transactions. This keeps each stage honest — you can query the database and immediately see which documents are waiting for the next step.

## Tests

Wrote a full test suite for both classes today — mocking out `pdfplumber` for the parser tests and using a real in-memory SQLite database for the `Ingestor` tests. The dedup logic, the hash computation, and the return value contracts are all covered.

---

The ingestor is done. Next up: the normalizer, which takes that wall of extracted PDF text and turns it into individual transactions.
