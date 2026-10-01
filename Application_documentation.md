## 1. Provisioning & Scaling New Instances

* **Horizontal Scaling (Same Environment):** To handle increased hospital traffic, you scale the stateless API containers, not the database. Using Docker Compose, run `docker-compose up -d --scale api=3`. A reverse proxy (NGINX or Traefik) must sit in front of these containers to distribute incoming requests across the active ports via round-robin or least-connections routing.
* **New Environment Provisioning (New Hospital Tenant):** When deploying for a completely new client, use Infrastructure as Code (Terraform or AWS CloudFormation) to automatically provision an isolated VPC, a new managed PostgreSQL instance, and a fresh Redis cache. Run the Alembic migration container once to initialize the blank schema, then boot the API containers.

## 2. Maintenance Procedures

* **Zero-Downtime Rolling Updates:** For routine code deployments, instruct your orchestrator (Kubernetes or Docker Swarm) to perform rolling updates. The load balancer stops sending traffic to Container A, updates it, waits for the `/health` endpoint to return 200, and then routes traffic back before proceeding to Container B. Clinical users experience zero interruption.
* **Hard Maintenance (Scheduled Downtime):** For critical infrastructure upgrades that require freezing data:
1. Update NGINX routing rules to serve a static HTTP 503 "Under Maintenance" JSON response for all `/api/*` routes.
2. Spin down all API and background worker containers to cleanly sever all active database connections.
3. Execute the core system upgrades.
4. Run internal integration tests against the live database.
5. Remove the 503 block at the NGINX layer to restore traffic.



## 3. Database Migrations (Zero-Downtime Strategies)

Executing `alembic upgrade` on a live Tier-1 database requires strict operational patterns to avoid locking tables and causing cascading API timeouts.

* **The Expand and Contract Pattern:** Never rename, change the type of, or delete a column that live traffic is actively querying.
* **Expand:** Create an Alembic script to add the new column. Run the migration.
* **Dual-Write:** Deploy API code that writes to both the old and new columns, and run a background script to backfill historical rows.
* **Contract:** Deploy an API update that reads/writes *only* to the new column. Finally, issue a cleanup migration to drop the legacy column.


* **Separation of Concerns:** Always run your migrations in a completely separate, ephemeral Docker container that executes and terminates before your newly updated API instances are allowed to boot.

## 4. Production Incident Response

* **Traceability via Correlation IDs:** When a clinical user reports a failure, ask for the `X-Request-ID` returned in their error payload. Search this exact ID in your structured log aggregator (e.g., Datadog, AWS CloudWatch, or ELK) to instantly isolate the request's journey through the routing, middleware, and database execution layers.
* **Handling Database Deadlocks:** The system utilizes `with_for_update(nowait=True)` for sensitive resources like inpatient beds and pharmacy inventory. If concurrent transactions collide, the database immediately rejects the slower transaction rather than hanging indefinitely. Ensure frontend applications are programmed to catch HTTP 409 Conflict errors and prompt the user to automatically retry.
* **Catastrophic Failure & Rollback:** If a deployment severely degrades performance, you must immediately revert the infrastructure state:
1. Revert the container image tag in your orchestrator to the previous stable release.
2. If the deployment included a backwards-incompatible database schema change, execute `alembic downgrade -1` to cleanly reverse the structural change.
3. If severe data corruption occurred, utilize your cloud provider's PostgreSQL Point-in-Time Recovery (PITR) to restore the database cluster to the exact minute preceding the bad deployment.
