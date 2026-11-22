# Order-to-Audit System for a Small Food Manufacturer

A Django and PostgreSQL application for managing the operational flow of a small food manufacturer, from customer orders and raw-material purchases through production, invoicing, dispatch, payments, inventory tracking, and audit reporting.

> **Project status:** The application has been checked locally and its test suite most recently reported **29 passing tests**. The benchmark figures below describe the demonstration database used during development; they are not automatically inserted into a fresh database.

## Contents

- [Overview](#overview)
- [Main features](#main-features)
- [User roles](#user-roles)
- [Technology](#technology)
- [Local setup](#local-setup)
- [Create users and configure roles](#create-users-and-configure-roles)
- [Application routes](#application-routes)
- [Demonstration benchmark](#demonstration-benchmark)
- [Reports and exports](#reports-and-exports)
- [Run tests](#run-tests)
- [Important configuration and limitations](#important-configuration-and-limitations)
- [Security notes](#security-notes)

## Overview

The system connects the sales, inventory, production, and accounting activities of a small food manufacturer. It is designed to provide traceability for orders, invoices, dispatches, payments, purchases, production runs, material consumption, and stock movements.

The principal business flow is:

1. A salesman creates and submits a customer order.
2. Factory Admin reviews and approves or rejects the order.
3. An invoice can be issued for the approved order.
4. Dispatch records track finished-goods movement and stock deduction.
5. Payments update the invoice's paid amount and outstanding balance.
6. Production records capture BOM-based material usage, finished output, wastage, and production costs.
7. Audit reports provide operational and financial summaries with PDF and Excel exports where available.

## Main features

### Sales and order management

- Dedicated, responsive Salesman portal.
- Salesmen can create and submit orders for their customers and view their own order history and status.
- Product selling prices are used when creating order lines.
- Factory Admin can review and manage submitted orders.
- Invoice records include taxable value, GST components, place of supply, and invoice numbering fields.
- Invoice PDF generation.
- Dispatch records and stock checks help prevent dispatching quantities that exceed available finished-goods stock.
- Payment tracking, invoice balances, and an owner-controlled payment-reversal workflow that preserves reversal history.

### Inventory and purchasing

- Raw-material purchases and purchase register.
- Inventory movement records for opening stock, receipts, consumption, and dispatches.
- Raw-material and finished-goods stock reporting.
- Excel and PDF exports for supported reports.

### Production and costing

- Bill of Materials (BOM) and product-recipe records.
- Production runs with planned and actual material consumption.
- Good output, wastage, and damaged packaging tracking.
- Production cost records for materials, labour, power/gas, and other costs.
- Production audit reporting, including material variance and cost summaries.

### Audit and management reporting

- Sales audit report.
- Stock movement report.
- Raw-material movement report.
- Production audit report.
- Purchase register.
- GST outward supply summary.
- Management dashboard.
- PDF and Excel exports for supported report types.

## User roles

The `setup_roles` management command creates or configures the application's standard permission groups.

| Role | Intended access |
|---|---|
| **Salesman** | Create and submit own sales orders; view own order and customer-related information permitted by the portal. |
| **Factory Admin** | Manage operational master data, purchases, sales-order review, dispatch, and related factory operations. |
| **Production Supervisor** | Manage BOMs, recipes, production runs, consumption, and production costs; view relevant stock information. |
| **Accountant/Auditor** | Read-only access to business records and audit/reporting information. |
| **Owner** | Broad administrative and oversight access, including controlled payment-reversal actions. |

Access is enforced through Django authentication, group permissions, and role checks in the application. Do not use a superuser account as a substitute for testing the individual roles.

## Technology

- Python (the development environment used for verification was Python 3.15)
- Django (the development environment used for verification was Django 6.1.3)
- PostgreSQL (the development environment used for verification was PostgreSQL 19)
- HTML, CSS, and JavaScript for the web interface
- OpenPyXL for Excel report generation
- ReportLab for PDF report generation

Dependency versions are recorded in `requirements.txt`. Use that file as the source of truth when installing the project's Python dependencies.

## Local setup

The commands below use Windows PowerShell. The project was developed on Windows, but the Django application can be adapted to other environments with the corresponding Python and PostgreSQL setup.

### 1. Clone the repository

```powershell
git clone <repository-url>
cd order-to-audit-system
```

Replace `<repository-url>` with the repository URL after the project has been pushed to your Git host.

### 2. Create and activate a virtual environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation in the current terminal, you can allow scripts for that PowerShell process only, then activate again:

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

Edit `.env` and provide values for your local environment. At minimum, configure:

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

The `.env.example` file also documents optional production-security and email settings. Keep real credentials and secret keys in `.env`, not in source control.

### 5. Create the PostgreSQL database

Create a PostgreSQL database matching `DB_NAME` in `.env`. For example, using `psql`:

```sql
CREATE DATABASE order_audit_db;
```

Use the database name, username, password, host, and port that match your `.env` configuration. You can also create the database using pgAdmin.

### 6. Apply migrations

```powershell
python manage.py migrate
```

### 7. Configure application roles

```powershell
python manage.py setup_roles
```

This command sets up the standard role groups and their permissions. Run it after database migrations.

### 8. Create an administrator account

```powershell
python manage.py createsuperuser
```

Follow the prompts to create your own administrator credentials.

### 9. Start the development server

```powershell
python manage.py runserver
```

Open the application at:

- Django Admin: `http://127.0.0.1:8000/admin/`
- Login: `http://127.0.0.1:8000/accounts/login/`
- Salesman portal: `http://127.0.0.1:8000/sales/salesman/`

A fresh database will contain the database schema and configured role groups after the steps above. The demonstration transactions and benchmark stock figures described later are not guaranteed to be created by these commands; load or create demonstration records separately.

## Create users and configure roles

1. Sign in to Django Admin with the superuser created during setup.
2. Create the required application users.
3. Assign each user to the appropriate group created by `python manage.py setup_roles`.
4. Grant staff access only to users who need Django Admin access. The Salesman portal is a separate role-protected interface.
5. Sign in with a non-superuser account to verify the permissions for each role.

For a meaningful access-control check, use separate test accounts for Salesman, Factory Admin, Production Supervisor, Accountant/Auditor, and Owner. Never publish passwords for local demonstration accounts in this README or in the public repository.

## Application routes

| Route | Purpose |
|---|---|
| `/admin/` | Django Admin and operational administration |
| `/accounts/` | Django authentication views, including login and logout |
| `/sales/salesman/` | Salesman dashboard |
| `/sales/salesman/orders/new/` | Create a new salesman order |
| `/sales/invoices/<invoice_id>/pdf/` | Generate an invoice PDF |
| `/reports/stock-movements/` | Stock movement report |
| `/reports/raw-materials/` | Raw-material movement report |
| `/reports/sales-audit/` | Sales audit report |
| `/reports/production-audit/` | Production audit report |
| `/reports/purchase-register/` | Purchase register |
| `/reports/gst-outward/` | GST outward supply summary |
| `/reports/management-dashboard/` | Management dashboard |

Report pages expose their corresponding export links where supported. See `reporting/urls.py` for the complete list of report and export routes.

## Demonstration benchmark

The development database was used to validate a Murukku 200 g benchmark scenario. These are demonstration values, not live business data.

### Production

- Planned batch quantity: 200 packets.
- Good output: 188 packets.
- Wastage: 12 packets (6%).
- Actual recorded raw-material consumption: 40 kg flour, 10.5 L oil, and 1.2 kg spice.
- Packaging recorded: 194 pouches (188 good plus 6 damaged) and 9 cartons.
- Recorded production cost: ₹5,855 (materials ₹4,055, labour ₹1,200, power/gas ₹600).
- Calculated cost per good packet: approximately ₹31.14.

### Sales and receivables

| Invoice | Customer / place of supply | Taxable value | GST | Invoice total | Payment recorded | Balance |
|---|---|---:|---:|---:|---:|---:|
| `INV/2026-27/0001` | Retailer A / Tamil Nadu | ₹7,500.00 | CGST ₹187.50 + SGST ₹187.50 | ₹7,875.00 | ₹4,000.00 | ₹3,875.00 |
| `INV/2026-27/0002` | Distributor B / Karnataka | ₹6,000.00 | IGST ₹300.00 | ₹6,300.00 | ₹0.00 | ₹6,300.00 |
| **Total** | | **₹13,500.00** | **₹675.00** | **₹14,175.00** | **₹4,000.00** | **₹10,175.00** |

The Distributor B order is for 120 packets. At the time of the benchmark check, 88 finished packets were available, so the 120-packet dispatch remained a draft with a shortage of 32 packets. The draft dispatch did not deduct stock.

### Selected stock balances

After the benchmark purchase, production, and Retailer A dispatch, the verified balances included:

| Item | Balance |
|---|---:|
| Flour | 120 kg |
| Oil | 19.5 L |
| Spice | 3.8 kg |
| Pouches | 306 |
| Cartons | 21 |
| Finished goods | 88 packets |

## Reports and exports

The reporting area provides operational and audit views for sales, purchases, inventory, production, and GST outward supplies. Supported reports can be exported to PDF and/or Excel.

The GST outward supply benchmark summary showed:

- 2 issued invoices
- Taxable value: ₹13,500.00
- CGST: ₹187.50
- SGST: ₹187.50
- IGST: ₹300.00
- Total GST: ₹675.00
- Grand total: ₹14,175.00

## Run tests

Run Django's system check:

```powershell
python manage.py check
```

Run the complete test suite:

```powershell
python manage.py test -v 2
```

The test suite most recently completed locally with **29 tests passing**. Run the tests again after making code or configuration changes.

## Important configuration and limitations

- The GST outward report's benchmark calculations were verified, but the demonstration invoice data did not have seller GSTIN and customer GSTIN values configured. HSN codes were also missing from the demonstrated invoice lines. Configure the appropriate business and customer master data before using the reports for real statutory work.
- The generated invoice PDF in the demonstration environment is explicitly marked as a demonstration document and not valid for tax-credit or legal tax use. Do not represent the current sample invoice as a legally compliant tax invoice.
- The benchmark numbers are stored in the development database used during testing. A newly created database will not automatically contain those transactions unless you separately load or recreate the sample data.
- The application was tested locally. A public production deployment requires environment-specific configuration, database provisioning, HTTPS, secure cookies, a production-grade secret key, correct `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, static-file handling, and an appropriate web server.

## Security notes

- Never commit `.env`, database dumps, backups, local credentials, or other secrets.
- `.env`, `.venv`, and the `backup/` directory are ignored by the project's `.gitignore` in the development repository.
- Use separate accounts to test role permissions. Avoid using the superuser for normal role testing because superusers bypass ordinary group restrictions.
- Rotate any temporary demonstration passwords that may have been exposed during development.
- Keep payment reversals and other sensitive actions restricted to authorized users, and retain the corresponding audit history.
