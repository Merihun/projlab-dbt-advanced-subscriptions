"""Sample Airflow DAG (Airflow 3). Files in airflow/dags/ are picked up by the optional Airflow service.

Airflow's container does not include dbt. To orchestrate dbt from Airflow, a common pattern is a
dedicated image with dbt installed, or a library such as astronomer-cosmos: that is part of the project.
"""

from datetime import datetime

from airflow.sdk import dag, task


@dag(schedule=None, start_date=datetime(2026, 1, 1), catchup=False, tags=["projlab"])
def projlab_hello():
    @task
    def extract() -> list[int]:
        return [1, 2, 3]

    @task
    def report(values: list[int]) -> None:
        print(f"processed {len(values)} rows")

    report(extract())


projlab_hello()
