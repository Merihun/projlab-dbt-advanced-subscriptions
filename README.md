# Subscription Revenue Platform: Advanced dbt at Scale

Start with **PROJECT.md**: what is provided, how to start the lab, the commands, the data and the definitions
the checks use.

The lab (`docker compose up -d --build --wait`) runs two services:

- `workspace`: Python 3.12 with dbt-core 1.12.5, dbt-postgres 1.11.0, dbt-duckdb 1.11.0, DuckDB, SQLFluff,
  pytest and ruff. Open a terminal in it with `docker compose exec workspace bash`; this folder is `/workspace`
  and is the dbt project.
- `postgres`: PostgreSQL 16, the warehouse of the `dev` and `prod` targets. It is not published to your
  machine; connect from the workspace (host `postgres`).

The `ci` target needs no service at all: it is the DuckDB file `fernhill.duckdb` in this folder.

The commands are in the `Makefile` (`make` lists them). Optional: Airflow (`docker compose --profile airflow up
-d`) is part of the lab template and not used by this project.

Recommended machine: 2 CPUs and 4 GB of memory available to Docker.
