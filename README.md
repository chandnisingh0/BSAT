# Bank Statement Analysis Tool — Django + MySQL (Ingestion Module)

This project lets you upload bank statements (CSV, XLSX, PDF, image, RPT),
extract the transactions, and store them in a MySQL database.

This is **Phase 1 (data ingestion only)**. Matching against related parties
comes later.

---

## What each file type uses

| File type            | Extracted using            | Needs OCR? |
|----------------------|----------------------------|------------|
| `.csv`, `.xlsx`      | pandas                     | No         |
| `.rpt` (text report) | text parser (auto-detect)  | No         |
| `.pdf` (text-based)  | pdfplumber                 | No         |
| `.pdf` (scanned)     | Tesseract OCR              | Yes        |
| `.jpg`, `.png`       | Tesseract OCR              | Yes        |

**You do NOT train any AI model.** Tesseract is a ready-made OCR engine you
just call. Prefer CSV / RPT files whenever possible — they are far more
accurate than OCR.

> About `.rpt` files: open one in Notepad first. If it shows readable text,
> this tool parses it. If it shows binary garbage, it's a Crystal Reports
> file and needs Crystal Reports / SAP tools to export to CSV first.

---

## STEP 1 — Install the prerequisites

1. **Python 3.11+** — https://www.python.org/downloads/
2. **MySQL Server** — https://dev.mysql.com/downloads/mysql/
   (or install XAMPP, which bundles MySQL, if you prefer a GUI)
3. **Tesseract OCR** (only needed for images/scanned PDFs)
   - Windows: https://github.com/UB-Mannheim/tesseract/wiki
   - Mac: `brew install tesseract`
   - Linux: `sudo apt install tesseract-ocr`
4. **Poppler** (only needed for scanned PDFs)
   - Windows: download poppler, add its `bin` folder to PATH
   - Mac: `brew install poppler`
   - Linux: `sudo apt install poppler-utils`

---

## STEP 2 — Create the MySQL database

Open MySQL command line (or phpMyAdmin) and run:

```sql
CREATE DATABASE bsa_db CHARACTER SET utf8mb4;
```

That's all — Django creates the tables for you in Step 5.

---

## STEP 3 — Set up the Python environment

Open a terminal in the project folder (where `manage.py` is) and run:

```bash
# create and activate a virtual environment
python -m venv venv

# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# install dependencies
pip install -r requirements.txt
```

> If `mysqlclient` fails to install on Windows, the easiest fix is:
> `pip install mysqlclient‑*.whl` from a prebuilt wheel, or use
> `pip install pymysql` and add `import pymysql; pymysql.install_as_MySQLdb()`
> at the top of `bsa_project/__init__.py`.

---

## STEP 4 — Configure your settings

1. Copy `.env.example` to `.env`
2. Open `.env` and fill in your MySQL password and (if using OCR) the
   Tesseract path.

```
DB_NAME=bsa_db
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_HOST=127.0.0.1
DB_PORT=3306
```

---

## STEP 5 — Create the database tables

```bash
python manage.py makemigrations statements
python manage.py migrate
```

This creates the `account`, `statement`, and `transaction` tables in MySQL.

---

## STEP 6 — Create an admin login

```bash
python manage.py createsuperuser
```

Follow the prompts (username, email, password).

---

## STEP 7 — Run the server

```bash
python manage.py runserver
```

Now open these in your browser:

- **http://127.0.0.1:8000/admin/** — log in, then add at least one **Account**
  (the bank account a statement belongs to). You must add an account before
  uploading.
- **http://127.0.0.1:8000/** — the upload page. Pick the account, choose a
  file, click **Upload & Extract**. The extracted transactions appear and are
  saved in MySQL.

---

## STEP 8 — Test it

Use the files in `sample_data/`:
- `sample_hdfc.csv` — a clean CSV (works immediately, no OCR)
- `sample_report.rpt` — a pipe-delimited RPT report (works immediately)

Upload either one to confirm everything is wired up before testing PDFs/images.

---

## How the code is organised

```
bsa_project/
├── manage.py                 # Django command runner
├── requirements.txt
├── .env.example              # copy to .env and fill in
├── bsa_project/              # project settings
│   ├── settings.py           # MySQL config is here
│   └── urls.py
└── statements/               # the app
    ├── models.py             # Account, Statement, Transaction tables
    ├── views.py              # upload + list logic
    ├── forms.py              # the upload form
    ├── admin.py              # admin screens
    ├── parsers/              # ⭐ extraction logic
    │   ├── base.py           # date/amount cleaning, text heuristic
    │   ├── csv_parser.py     # CSV/XLSX (pandas)
    │   ├── pdf_parser.py     # pdfplumber + OCR fallback
    │   ├── image_parser.py   # Tesseract OCR
    │   ├── rpt_parser.py     # text RPT files
    │   └── __init__.py       # dispatcher: picks parser by extension
    └── templates/statements/ # the web pages
```

---

## Important honest notes

1. **OCR is messy.** Images and scanned PDFs will have errors. The parser
   makes a best effort and flags the file for review — always check the rows
   against the original. Push for CSV/RPT/text-PDF exports from the bank
   wherever you can.

2. **The column mapping is simple in this version.** The CSV parser matches
   common column names automatically. If a bank uses an unusual column name,
   add it to `COLUMN_ALIASES` in `csv_parser.py`.

3. **PDF table positions vary by bank.** `pdf_parser.py` assumes columns are
   in the order date / narration / debit / credit / balance. Some banks differ;
   you adjust the positional mapping per bank as you encounter them.

4. **Never commit your `.env` file or real bank statements to git.** This is
   confidential CIRP data.
