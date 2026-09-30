# School Library System — نظام إدارة المكتبة المدرسية

Flask + SQLAlchemy PWA, Arabic (RTL) / English (LTR). Modeled on Follett Destiny workflows.

## Phase 1 (this commit)
- School settings, academic years, semesters
- Stages → Classes → Sections (per academic year)
- Students: manual add/edit, CSV/XLSX import with preview & validation
- Bulk move (multi-select) and year promotion with preview
- Operations log with undo (move / promote)
- PWA manifest + service worker, ar/en switch

## Run
```
pip install -r requirements.txt
export SECRET_KEY=... 
flask --app wsgi create-admin admin 'StrongPassword'
flask --app wsgi run
```
Set `DATABASE_URL` for PostgreSQL in production (defaults to SQLite).

## Roadmap
2. Catalog, barcode range generation (Code 39), scanning, circulation
3. Student portal (ISBN / shelf / location search), holds
4. Azure Graph email notices, statistics, comparisons, exports
