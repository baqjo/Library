# School Library System — نظام إدارة المكتبة المدرسية

Flask + SQLAlchemy PWA, Arabic (RTL) / English (LTR). Modeled on Follett Destiny workflows.

## Phase 1
- School settings, academic years, semesters
- Stages → Classes → Sections (per academic year)
- Students: manual add/edit, CSV/XLSX import with preview & validation
- Bulk move (multi-select) and year promotion with preview
- Operations log with undo (move / promote)
- PWA manifest + service worker, ar/en switch

## Phase 2
- Catalog: titles (ISBN with checksum check, author, category, call number) and copies (shelf, location, price, status); search by ISBN / title / author / copy barcode; CSV/XLSX import
- Barcodes: manual ranges (prefix + start/end + zero padding) for book copies and students; Code 39 labels (number printed under the bars), printable sheets with column count and "skip used cells", student cards, bulk assignment to students without a barcode
- Circulation desk for USB scanner guns (keyboard wedge: types the code + Enter; `*` start/stop characters are stripped), optional camera scanning where `BarcodeDetector` exists
- Check out / check in / renew; policy (loan days, max loans, renewals, fine per day, grace days, weekly closed days, holidays); fines with pay / waive; lost copies bill their price
- Every loan stores year, semester and the student's section at loan time (used for the statistics in phase 4)

## Run
```
pip install -r requirements.txt
export SECRET_KEY=... 
flask --app wsgi create-admin admin 'StrongPassword'
flask --app wsgi run
```
Set `DATABASE_URL` for PostgreSQL in production (defaults to SQLite). Set `SCHOOL_TZ` (e.g. `Asia/Amman`) so due dates use the school's local date, and `SESSION_COOKIE_SECURE=1` behind HTTPS.

Note: tables are created with `create_all()`, so upgrading from phase 1 adds the new tables without touching existing ones.

## Roadmap
3. Student portal (ISBN / shelf / location search), holds
4. Azure Graph email notices, statistics, comparisons, exports
