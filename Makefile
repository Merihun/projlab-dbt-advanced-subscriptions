help:  ## list the targets (the default: plain `make` never rebuilds or reloads anything)
	@grep -E '^[a-z-]+:.*## ' Makefile | sed 's/:.*## /\t/'

.PHONY: help data load build build-ci test-ci freshness counts parse docs ci-state slim lint test check clean

# The Python to run. On a Mac or Linux machine without a `python` command: make data PYTHON=python3
PYTHON ?= python
# dbt as installed in the lab. Elsewhere: make build DBT="python -m dbt.cli.main"
DBT ?= dbt
TARGET ?= dev
BATCH ?= 1
# The pull request whose build `make slim` imitates. dbt-ci.yml uses the real number (pr_42_finance for #42).
PR ?= local

data:  ## (re)write data/batch_1/, data/batch_2/ and the two seeds, always the same bytes
	$(PYTHON) -m fernhill_subs generate

load:  ## load a batch into raw: make load BATCH=1 TARGET=dev (dev/prod: PostgreSQL, ci: DuckDB)
	$(PYTHON) -m fernhill_subs load --batch $(BATCH) --target $(TARGET)

build:  ## dbt build on a target: make build TARGET=dev
	$(DBT) build --target $(TARGET)

build-ci:  ## the ci target from nothing: both batches into DuckDB, a build after each
	rm -f fernhill.duckdb
	$(PYTHON) -m fernhill_subs load --batch 1 --target ci
	$(DBT) build --target ci
	$(PYTHON) -m fernhill_subs load --batch 2 --target ci
	$(DBT) build --target ci

freshness:  ## dbt source freshness on a target
	$(DBT) source freshness --target $(TARGET)

counts:  ## rows per source table (your source_row_counts macro)
	$(DBT) run-operation source_row_counts --target $(TARGET)

parse:  ## dbt parse: the project, the semantic layer and the contracts' YAML are valid
	$(DBT) parse --target $(TARGET)

docs:  ## the docs site on http://localhost:8081
	$(DBT) docs generate --target $(TARGET) && $(DBT) docs serve --host 0.0.0.0 --port 8081

ci-state:  ## step 10: build ci from nothing and keep its manifest in state/ (production's state)
	$(MAKE) build-ci
	mkdir -p state && cp target/manifest.json state/manifest.json

slim:  ## step 10: build what changed since state/, and its children, into pr_$(PR)_* (make slim PR=42)
	DBT_CI_SCHEMA=pr_$(PR) $(DBT) build --target ci --selector slim_ci --defer --state state

lint:  ## SQLFluff on the models (dbt templater, ci target) and ruff on the Python
	$(PYTHON) -m sqlfluff lint models
	$(PYTHON) -m ruff check fernhill_subs tests

test:  ## the lab's own tests (is everything installed and reachable?)
	$(PYTHON) -m pytest -q tests/test_environment.py

check:  ## the project's checks on your machine: needs the local check kit in .projlabs/ (Review page)
	$(PYTHON) .projlabs/check.py

clean:  ## remove dbt's target/ and logs/
	$(DBT) clean
