# Order-to-Audit System for a Small Food Manufacturer

A Django and PostgreSQL application for managing a small food manufacturer's workflow—from field-sales orders and raw-material purchases through production, GST invoice generation, dispatch, collections, inventory, costing, and audit reporting.

> **Development status:** The latest complete local test run reported **77 passing tests** and `python manage.py check` reported no issues. The application has been verified locally on Windows with PostgreSQL. A public production deployment and the final submission video are not yet included.

## Contents

- [Overview](#overview)
- [Features](#features)
- [User roles](#user-roles)
- [Technology and design choices](#technology-and-design-choices)
- [Data model and transaction traceability](#data-model-and-transaction-traceability)
- [Business workflows](#business-workflows)
- [Inventory and costing assumptions](#inventory-and-costing-assumptions)
- [Local setup](#local-setup)
- [Load benchmark master data](#load-benchmark-master-data)
- [Create users and configure roles](#create-users-and-configure-roles)
- [Application routes](#application-routes)
- [Benchmark results](#benchmark-results)
- [Reports and exports](#reports-and-exports)
- [Tests and verification](#tests-and-verification)
- [Known gaps and limitations](#known-gaps-and-limitations)
- [Submission checklist](#submission-checklist)
- [Security notes](#security-notes)

## Overview

The system connects sales, inventory, production, and accounting activities for a small packaged-snack manufacturer. It is designed to reduce duplicate data entry and provide traceability for orders, invoices, dispatches, payments, purchases, production runs, material consumption, costs, and stock movements.

The two main workflows meet at finished-goods inventory:

**Sales loop:** salesman order → factory review and approval → GST invoice → dispatch → finished-goods stock reduction → payment and receivables.

**Production loop:** raw-material purchase/receipt → BOM-based material issue → production run → finished-goods output → variance and costing.

The application provides operational and month-end reporting. It is a demonstration prototype, not a replacement for statutory accounting or tax software.

## Features

### Sales and order management

- Responsive salesman portal for selecting customers, entering products and quantities, and submitting orders.
- Salesmen can view their own orders, status, and customer-wise sales.
- Factory Admin can review submitted orders and approve or reject them.
- Invoice generation for approved orders, with financial-year-based numbering.
- Invoice snapshots retain seller, customer, place-of-supply, HSN, price, and GST information as recorded at issue time.
- Demonstration GST calculations support CGST/SGST for intra-state supplies and IGST for inter-state supplies.
- Invoice PDF generation.
- Dispatch posting checks finished-goods stock and remaining order quantities.
- Payment recording, invoice balances, and owner-controlled payment reversals with reversal reason and history.

### Credit notes and invoice audit

- Credit notes are issued against existing issued invoices through a dedicated workflow.
- Credit-note lines link to original invoice lines and retain product, HSN, quantity, price, and tax snapshots.
- The service validates positive whole-number quantities and prevents cumulative credited quantities from exceeding original invoice-line quantities.
- Credit-note amounts are calculated from original invoice snapshots, including the applicable CGST/SGST or IGST split.
- Partial credit notes leave an invoice issued. When cumulative credit notes fully offset the original invoice total, the invoice is marked cancelled and an invoice cancellation audit event is recorded.
- Original invoices and payments are not deleted. If received payments exceed the credit-adjusted invoice value, the excess is shown as customer credit; it is not treated as an automatic refund.
- Credit-note and invoice audit records are viewable in Django Admin but cannot be manually added, edited, or deleted there.
- Issued invoice fields and invoice lines are read-only in Django Admin.

### Inventory and purchasing

- Raw-material and packaging purchases and purchase register.
- Inventory movement records for opening stock, receipts, production consumption, dispatches, and supported adjustments.
- Separate tracking for raw materials, packaging, and finished goods.
- Ledger-based stock reporting and configured low-stock/reorder alerts.
- Excel and PDF exports for supported reports.

### Production and costing

- Bill of Materials (BOM) and product-recipe records.
- Production runs with planned and actual material consumption.
- Good output, wastage, and damaged packaging tracking.
- Posting a production run records raw-material issues and finished-goods output.
- Production audit history for creation, draft updates, and posting.
- Production cost records for materials, labour, power/gas, and other costs.
- Reports for material variance, yield variance, and cost per good packet.

### Audit and management reporting

- Sales audit report.
- Stock movement and raw-material movement reports.
- Production audit report.
- Purchase register.
- GST outward-supply summary.
- Customer outstanding and ageing report.
- Management dashboard.
- PDF and Excel exports for supported reports.

## User roles

The `setup_roles` management command creates or configures the standard permission groups.

| Role | Intended access |
|---|---|
| **Salesman** | Create and submit own sales orders; view own order history, status, and permitted customer-wise sales information. |
| **Factory Admin** | Review orders, manage operational records, issue credit notes, manage purchases and dispatches, and perform factory administration. |
| **Production Supervisor** | Manage BOMs, recipes, production runs, consumption, and production costs; view relevant stock information. |
| **Accountant/Auditor** | Read-only access to permitted business records, credit notes, audit logs, and reports. |
| **Owner** | Business oversight and broad administrative access, including controlled payment-reversal and credit-note actions. |

Access is enforced through Django authentication, group permissions, and application role checks. A Django superuser bypasses ordinary group restrictions and should not be used as a substitute for testing each role.

## Technology and design choices

- **Python and Django:** Django supplies the web framework, ORM, authentication, permissions, migrations, and administrative interface. This suits a role-based business workflow with related transactional records.
- **PostgreSQL:** A relational database is used for structured business records, referential integrity, and transactional updates.
- **HTML, CSS, and JavaScript:** Used for responsive browser interfaces and the credit-note quantity/GST preview.
- **OpenPyXL:** Used for Excel report generation.
- **ReportLab:** Used for PDF report generation.

The development environment used for the latest local verification was Python 3.15, Django 6.1.3, and PostgreSQL 19. Dependency versions are recorded in `requirements.txt`; use that file as the source of truth when installing dependencies.

## Data model and transaction traceability

The main model relationships are:

- A **Customer** can have multiple **SalesOrders**.
- A **SalesOrder** has **SalesOrderItems** and can be converted into an **Invoice**.
- An **Invoice** has **InvoiceLines**, **Payments**, **CreditNotes**, and **InvoiceAuditLogs**.
- A **CreditNote** belongs to an invoice and contains **CreditNoteLines** linked to original invoice lines.
- **Products** are used in sales and finished-goods inventory.
- **Product recipes/BOMs** define standard raw-material and packaging requirements.
- A **ProductionRun** records planned and actual consumption, good output, wastage, and production costs.
- **StockMovements** record quantity changes with movement types and source references such as purchase, production, or dispatch.

Money is represented with Django `DecimalField` values and calculated with Python `Decimal`, not binary floating-point arithmetic. Invoice and credit-note line snapshots preserve values used at the time of issue.

Stock balances are calculated from recorded movements rather than relying solely on a manually overwritten quantity.

## Business workflows

### Sales order to collection

1. Salesman signs in to the portal.
2. Salesman selects an active customer and adds products and quantities.
3. The order is submitted for factory review.
4. Factory Admin approves or rejects it.
5. An approved order can be invoiced. Invoice numbering uses an Indian financial year (April to March).
6. Dispatch posting checks stock and records outgoing finished-goods movements.
7. Payments are recorded against the issued invoice.
8. Outstanding and ageing reports calculate receivables using invoice values, received payments, and credit notes as of the selected report date.

### Production run

1. Factory Admin or Production Supervisor maintains product recipes/BOMs and stock records.
2. A production run is created for a product and batch quantity.
3. Expected output and planned raw-material/packaging requirements are derived from the recipe.
4. Actual consumption, good output, damaged packaging, and wastage are recorded.
5. Posting the run records raw-material issues and finished-goods output.
6. Production reports compare planned and actual consumption and yield, and show production cost per good packet.

### Credit note and invoice cancellation

1. Owner or Factory Admin selects an issued invoice in the credit-note workflow.
2. Returned quantities are entered against original invoice lines with a reason and credit-note date.
3. The service validates remaining uncredited quantities and calculates the credit from original invoice snapshots.
4. The credit note is saved with a unique financial-year-based number.
5. Partial credits leave the invoice issued. When total credits equal the original invoice amount, the invoice is marked cancelled and the cancellation is written to the invoice audit log.
6. Original payment records remain intact. Any excess amount received is exposed as customer credit and is not automatically refunded.

## Inventory and costing assumptions

### Stock calculation

Stock is represented by a movement ledger. The general balance equation is:

`Opening balance + receipts + production output - production issues - dispatches ± adjustments = closing balance`

The benchmark scenario keeps raw materials, packaging items, and finished goods in separate balances and units.

### Valuation method

The implementation uses a **weighted-average** raw-material valuation method. The weighted-average rate is calculated from available stock value and quantity together with new purchase receipts. Material issue cost uses the applicable weighted-average rate.

The benchmark production cost is calculated as:

`Material and packaging cost + direct labour + power/gas and other run overheads`

`Cost per good packet = Total production-run cost / Actual good packets`

For the benchmark run, the recorded material/packaging cost is ₹4,055, labour is ₹1,200, and gas/power is ₹600. Total run cost is ₹5,855. Dividing by 188 good packets gives approximately ₹31.14 per good packet. Direct labour and power/gas are assigned to that production run.

These are prototype costing assumptions for the supplied benchmark, not a complete statutory inventory valuation or financial-accounting implementation.

## Local setup

The commands below use Windows PowerShell. The application was developed and verified locally on Windows. Other operating systems can be used with equivalent Python and PostgreSQL commands.

### 1. Clone the repository

```powershell
git clone <repository-url>
cd order-to-audit-system
```

Replace `<repository-url>` with the public repository URL after pushing the project.

### 2. Create and activate a virtual environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation in the current terminal, allow scripts for that process only and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 3. Install Python dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Create the environment file

```powershell
Copy-Item .env.example .env
```

Edit `.env` and provide local values. At minimum:

```dotenv
SECRET_KEY=replace-with-a-long-random-secret
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

DB_NAME=order_audit_db
DB_USER=your_postgresql_username
DB_PASSWORD=your_postgresql_password
DB_HOST=localhost
DB_PORT=5432
```

The `.env.example` file documents optional production-security and email settings. Keep real credentials and secret keys in `.env`, not in source control.

### 5. Create the PostgreSQL database

Create a PostgreSQL database matching `DB_NAME` in `.env`. For example, using `psql`:

```sql
CREATE DATABASE order_audit_db;
```

You can also create the database using pgAdmin.

### 6. Apply migrations

```powershell
python manage.py migrate
```

### 7. Configure application roles

```powershell
python manage.py setup_roles
```

Run this after migrations.

### 8. Load benchmark master data

```powershell
python manage.py load_benchmark_data
```

The benchmark loader is designed to be safe to rerun and to avoid duplicating existing opening-stock records. It prepares/validates benchmark master and opening data. Follow the benchmark workflow to create and verify purchase, production, order, invoice, dispatch, and payment transactions. Do not assume a fresh database contains every benchmark transaction merely because the loader has run.

### 9. Create an administrator account

```powershell
python manage.py createsuperuser
```

Follow the prompts to create local administrator credentials.

### 10. Start the development server

```powershell
python manage.py runserver
```

Open:

- Django Admin: `http://127.0.0.1:8000/admin/`
- Login: `http://127.0.0.1:8000/accounts/login/`
- Salesman portal: `http://127.0.0.1:8000/sales/salesman/`
- Credit-note register: `http://127.0.0.1:8000/sales/credit-notes/`

A fresh database has the schema and role groups after migrations and role setup. Benchmark transaction records are separate from the schema and must be created or loaded as described above.

## Create users and configure roles

1. Sign in to Django Admin with the superuser created during setup.
2. Create the required application users.
3. Assign each user to the appropriate group created by `python manage.py setup_roles`.
4. Grant Django staff access only to users who need Django Admin access. The Salesman portal is a separate role-protected interface.
5. Sign in with a non-superuser account to verify each role's permissions.

Use separate local test accounts for Salesman, Factory Admin, Production Supervisor, Accountant/Auditor, and Owner. Do not publish local demonstration passwords in this README or the public repository. Create fresh local test accounts instead of relying on credentials from the development database.

## Application routes

| Route | Purpose |
|---|---|
| `/admin/` | Django Admin and operational administration |
| `/accounts/` | Django authentication views, including login and logout |
| `/sales/salesman/` | Salesman dashboard |
| `/sales/salesman/orders/new/` | Create a new salesman order |
| `/sales/invoices/<invoice_id>/pdf/` | Generate an invoice PDF |
| `/sales/credit-notes/` | Credit-note register |
| `/sales/credit-notes/create/<invoice_id>/` | Issue a credit note against an invoice |
| `/sales/credit-notes/<credit_note_id>/` | View a credit-note detail record |
| `/reports/stock-movements/` | Stock movement report |
| `/reports/raw-materials/` | Raw-material movement report |
| `/reports/sales-audit/` | Sales audit report |
| `/reports/production-audit/` | Production audit report |
| `/reports/purchase-register/` | Purchase register |
| `/reports/gst-outward/` | GST outward-supply summary |
| `/reports/management-dashboard/` | Management dashboard |

The reporting application also includes customer outstanding/ageing and export routes. See `reporting/urls.py` for exact paths.

## Benchmark results

The development database was used to validate the required Murukku 200 g scenario. These are demonstration values, not live business data.

### Production and inventory

The benchmark recipe expects 200 packets per batch. The run recorded 40 kg flour, 10.5 L oil, and 1.2 kg spice mix as actual raw-material consumption; 188 good packets were produced, 6 pouches were damaged, and 9 cartons were used.

| Check | Calculation | Verified result |
|---|---:|---:|
| Flour closing | 100 + 60 - 40 | 120 kg |
| Cooking oil closing | 30 - 10.5 | 19.5 L |
| Spice mix closing | 5 - 1.2 | 3.8 kg |
| Pouches closing | 500 - 188 - 6 | 306 pieces |
| Cartons closing | 30 - 9 | 21 pieces |
| Finished goods after production | 50 + 188 | 238 packets |
| Finished goods after Retailer A dispatch | 238 - 150 | 88 packets |
| Yield variance | (188 - 200) / 200 | -6% |
| Oil consumption variance | (10.5 - 10) / 10 | +5% |

The Distributor B order is for 120 packets. After Retailer A's dispatch, 88 finished packets remained, leaving a shortage of 32 packets. The insufficient-stock dispatch remains unposted and does not deduct stock.

### Production cost

| Cost component | Amount |
|---|---:|
| Materials and packaging | ₹4,055.00 |
| Labour | ₹1,200.00 |
| Gas and power | ₹600.00 |
| **Total run cost** | **₹5,855.00** |
| Good output | 188 packets |
| **Cost per good packet** | **Approximately ₹31.14** |

Cost per good packet is total run cost divided by actual good output.

### Sales and receivables

| Invoice | Customer / place of supply | Taxable value | GST | Invoice total | Payment recorded | Balance |
|---|---|---:|---:|---:|---:|---:|
| `INV/2026-27/0001` | Retailer A / Tamil Nadu | ₹7,500.00 | CGST ₹187.50 + SGST ₹187.50 | ₹7,875.00 | ₹4,000.00 | ₹3,875.00 |
| `INV/2026-27/0002` | Distributor B / Karnataka | ₹6,000.00 | IGST ₹300.00 | ₹6,300.00 | ₹0.00 | ₹6,300.00 |
| **Benchmark total** | | **₹13,500.00** | **₹675.00** | **₹14,175.00** | **₹4,000.00** | **₹10,175.00** |

A separate QA test invoice of ₹52.50 exists in the development database. It is not part of the two-invoice benchmark total and should be excluded when comparing benchmark receivables. Therefore, an all-records management dashboard can show ₹10,227.50 outstanding while the two benchmark invoices alone total ₹10,175.00.

The supplied benchmark assumes a 5% GST rate for the exercise. Demo GSTIN values are placeholders, not real GST registrations. The generated invoice PDF is explicitly marked as a demonstration document and is not valid for tax-credit or legal tax use.

## Reports and exports

The reporting area provides views and supported exports for:

- Sales audit and sales-register information.
- GST outward-supply summary.
- Purchase register.
- Stock movement and raw-material movement.
- Production audit, planned-versus-actual consumption, yield, and cost.
- Customer outstanding and ageing.
- Management dashboard.

Export formats vary by report; use the export links shown on each report page. The GST outward summary is shaped for review and mapping to statutory reporting, but this prototype is not a GST return filing or government-connected system.

## Tests and verification

Run Django's system check:

```powershell
python manage.py check
```

Check for missing migrations:

```powershell
python manage.py makemigrations --check --dry-run
```

Run the complete test suite:

```powershell
python manage.py test
```

The latest complete local run reported **77 tests passing**. The test suite covers invoice GST calculations and snapshots, payment reversal, production audit history, credit-note calculations and permissions, invoice audit events, and outstanding-ageing reports. Run tests again after making code or configuration changes.

## Known gaps and limitations

- **Offline order capture and later synchronisation are not implemented.** The salesman portal requires network connectivity.
- **Batch/lot and expiry tracking with first-expiry-first-out dispatch are not implemented.**
- **Tally-compatible export, e-Invoice, and e-Way Bill integration are not implemented.**
- **Public production hosting is not yet configured.** Deployment requires a provisioned database, HTTPS, secure cookies, production secret key, correct host/origin settings, static-file handling, and a production web server.
- Demo GSTIN values are placeholders and the benchmark uses the assignment's assumed 5% GST rate. Confirm actual GSTIN, HSN classification, and applicable rates before real-world use.
- Older benchmark invoice records were created before later invoice snapshot/audit improvements. Their stored snapshots are not rewritten, and they do not automatically receive historical invoice-audit events. New invoices generated through the current service receive issuance snapshots and audit records.
- Credit notes reduce receivables using their issue date in the ageing report. A fully credited invoice is marked cancelled. Existing payments are preserved; settlement of any resulting customer credit/refund requires an explicit business process.
- The development database contains test/QA data as well as benchmark transactions. Use the benchmark rows and totals documented above rather than assuming every dashboard aggregate is benchmark-only.
- The benchmark loader prepares/validates benchmark master/opening data; create or load transaction records separately when setting up a fresh demonstration database.

## Submission checklist

Before submitting the internship task, complete and verify these items:

- [ ] Push the repository to a **public** GitHub or GitLab project and verify access without signing in.
- [ ] Include the commit history and ensure `.env`, credentials, database dumps, and backups are not tracked.
- [ ] Include a Design Note of no more than two pages (PDF or Markdown) with an ER diagram, stock/BOM calculation explanation, and costing/valuation method.
- [ ] Record a demo video of no more than eight minutes showing the benchmark scenario in the specified order, including both the mobile salesman view and admin view.
- [ ] Provide a local run command or hosted demo link.
- [ ] Prepare separate test logins for all five roles; share credentials securely with the evaluator rather than publishing passwords in this repository.
- [ ] Re-run migrations and the test suite from a clean database/environment before final submission.

## Security notes

- Never commit `.env`, database dumps, backups, local credentials, or other secrets.
- `.env`, `.venv`, and `backup/` should remain ignored by `.gitignore`.
- Use separate accounts to test role permissions. Avoid using the superuser for normal role testing because superusers bypass ordinary group restrictions.
- Rotate temporary demonstration passwords if they may have been exposed.
- Keep payment reversals, credit-note issuance, and other sensitive actions restricted to authorized users, and retain their audit history.
- Use dummy benchmark/customer data only. Do not add real customer or company information to a public demonstration repository.
