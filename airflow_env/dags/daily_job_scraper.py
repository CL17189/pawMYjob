# airflow_env/dags/daily_job_scraper.py
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta
import os

DEFAULT_WORKER_PY = os.environ.get("WORKER_PY", "/Users/lisa/Desktop/pawMYjob/airflow_env/.venv/bin/python")
# replace /FULL/PATH/TO/job_agent with your project absolute path if not using env var

default_args = {
    'owner': 'you',
    'depends_on_past': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='daily_job_scraper',
    default_args=default_args,
    start_date=datetime(2025, 1, 1),
    schedule_interval='0 23 * * *',  # daily 23:00 (you may adjust)
    catchup=False,
    max_active_runs=1,
) as dag:

    run_worker = BashOperator(
        task_id='run_job_agent_worker',
        bash_command=(
            f'{DEFAULT_WORKER_PY} -m worker_env.src.langgraph_flow '
            f'--resume /Users/lisa/Desktop/pawMYjob/worker_env/stored_data/resume.md '
            f'--query "data engineer"'
        ),
        env={"WORKER_PY": DEFAULT_WORKER_PY},
    )
