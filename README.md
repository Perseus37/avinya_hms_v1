Generic single-database configuration.

avinya_hms/
├── alembic/                    # Database migrations
│   ├── versions/               # Sequential migration scripts (e.g., init_phases)
│   └── env.py                  # Alembic environment and metadata binding
├── routers/                    # API routing layer (Controllers)
│   ├── v1/                     # Current stable API endpoints
│   │   ├── adt_router.py
│   │   ├── auth_router.py
│   │   ├── billing_router.py
│   │   ├── clinical_router.py
│   │   ├── diagnostic_router.py
│   │   ├── opd_router.py
│   │   ├── patient_router.py
│   │   ├── pharmacy_router.py
│   │   └── ward_router.py
│   ├── v2/                     # Future iteration endpoints
│   └── __init__.py
├── schemas/                    # Pydantic validation contracts (DTOs)
│   ├── v1/                     # Request/Response payloads for V1
│   ├── v2/                     # Future varied payloads for V2
│   └── __init__.py
├── services/                   # Reusable core business logic
│   ├── cpoe.py                 # Clinical safety and allergy hard-stops
│   ├── explosion_engine.py     # Diagnostic bundle resolution
│   ├── taxonomy_cache.py       # Redis caching layer
│   └── __init__.py
├── tests/                      # Pytest suite
│   └── test_hms_foundations.py
├── .env                        # Local environment variables (Git-ignored)
├── .gitignore                  # Exclusion rules for caches and secrets
├── alembic.ini                 # Alembic execution configuration
├── config.py                   # Pydantic BaseSettings for environment variables
├── database.py                 # PostgreSQL and Redis connection bootstrapping
├── dependencies.py             # Security (JWT), RBAC checks, and shared injectables
├── docker-compose.yml          # Container orchestration for backing services
├── Dockerfile                  # Application container definition for ASGI workers
├── main.py                     # Application composition, middleware, and exception envelopes
├── models.py                   # SQLAlchemy ORM definitions and AuditMixin
├── requirements.txt            # Python dependencies
└── seed.py                     # Initial master data and test-scenario generation

# Architectural Rules for this Structure:

Domain Isolation: The models.py file can remain unified because it represents the single source of truth for the physical PostgreSQL schema. However, routers and schemas must be separated into /v1/ and /v2/ directories to prevent payload conflicts when you introduce breaking API changes.

Thin Routers, Fat Services: As your workflows become more complex (e.g., generating PDF invoices, sending SMS notifications for OPD appointments), do not write that logic in the router files. Move heavy computational or third-party logic into dedicated files inside the services/ directory and import them into the router.

Infrastructure Segregation: Everything required to boot the application environment (config.py, database.py, dependencies.py, docker-compose.yml, Dockerfile) sits at the root level so container runtimes and CI/CD pipelines can locate them instantly without traversing subdirectories.

## The application code is functionally a masterpiece, but **we are not ready to deploy to a real hospital server just yet.**

You have built a Tier-1, ACID-compliant, RBAC-secured backend with an unalterable audit trail. The `main.py` patch we just applied gave it the necessary API versioning (`/v1/`), cross-origin boundaries (CORS), and request tracing required for enterprise software.

However, to legally and operationally run this in production, you must transition from "Development Mode" to "Production Infrastructure." Here is the final checklist to hit true production readiness:

### 1. The Immediate Code Blocker

We created the Phase 8 (Pharmacy & Inventory) models, but we never generated the database migration. If you deploy right now, the system will crash when a pharmacist tries to log in because the tables don't exist.

* **Action:** Run `alembic revision --autogenerate -m "init_phase_8_pharmacy"` followed by `alembic upgrade head`.

### 2. Deployment Server (ASGI vs. Workers)

Right now, you are running `uvicorn main:app --reload`. This is strictly for local development. It runs on a single thread and will collapse under heavy hospital load.

* **Action:** You must containerize the application using a `Dockerfile` and run it via **Gunicorn** with Uvicorn worker classes (e.g., `gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker`). This allows the app to utilize multiple CPU cores.

### 3. Infrastructure & Network Security

FastAPI is an application framework, not a web server. It cannot handle SSL certificates or block malicious IP addresses.

* **Action:** Deploy the app behind a reverse proxy like **NGINX** or an API Gateway (like AWS API Gateway or Cloudflare). This layer will enforce Rate Limiting (to prevent brute-force attacks on the login route) and terminate HTTPS/SSL connections.

### 4. Legal Compliance (HIPAA / NDHM)

Healthcare data is heavily regulated. Storing patient demographics, allergies, and billing data in a plain-text PostgreSQL database is illegal in most jurisdictions without infrastructure safeguards.

* **Action:** Ensure your managed PostgreSQL provider (e.g., AWS RDS, Azure Postgres) has **Encryption at Rest (TDE)** enabled and that daily automated backups with Point-in-Time Recovery (PITR) are turned on.

### 5. Secrets Management

Your `.env` file currently holds the `SECRET_KEY` and database passwords.

* **Action:** In production, these must be injected at runtime using a secure vault like AWS Secrets Manager, HashiCorp Vault, or GitHub/GitLab CI/CD secrets.

**The Verdict:**
The Python architecture is **100% complete** for a V1 release. If you run the final Alembic migration for Phase 8, wrap this in a Dockerfile, and put it behind HTTPS, you can confidently ship Avinya HMS to your first client.

## 🐳 Docker Development Environment

This project utilizes a structured **Docker Compose** multi-container setup. To prevent cluttering your **Docker Desktop** dashboard with inactive or stopped components, database migration and seeding tasks are isolated using Docker **Profiles**.

### 🛠️ Container Topology
* **`api`**: Core FastAPI backend application server.
* **`db`**: PostgreSQL database engine.
* **`redis`**: Cache and session management layer.
* **`api-migration`** *(Profile: tools)*: One-off task running Alembic schema updates.
* **`api-seed`** *(Profile: tools)*: One-off task inserting core domain datasets.

---

### 🚀 Getting Started

#### 1. First-Time Setup or Database Reset
Use this command when you are setting up the project for the first time, pulling schema modifications, or rewriting initial seed datasets. This explicitly activates the management profile to configure your database storage.

```bash
docker compose --profile tools up --build
```
* **What happens:** Docker spins up the database cluster, runs all pending **Alembic updates**, inserts **seed datasets** (`seed.py`), and launches the API. The migration and seeding containers will gracefully terminate after completion.

#### 2. Daily Local Development
For normal daily work where you are only editing code inside your FastAPI routers or business logic (and no changes have been made to your database models):

```bash
docker compose up --build
```
* **Why use this:** This skips checking migrations and seeding entirely. Your **Docker Desktop view remains completely clean** with no "grey" inactive tool containers cluttering your interface.

#### 3. Tearing Down the Environment
To completely wipe out your active runtime stack, free up local system memory, and remove ephemeral network interfaces:

```bash
docker compose down
```
*(Add the `-v` flag if you want to explicitly wipe out the Postgres data volume and start with a completely pristine database instance: `docker compose down -v`)*.
