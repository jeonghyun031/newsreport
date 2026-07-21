"""
KBO 데일리 AI 뉴스 브리핑 정기 구독 이메일 자동 발송 DAG
매일 아침 08:00 (KST)에 정기 구독자들에게 선택 관심 구단의 최신 AI 브리핑 이메일을 자동 발송합니다.
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import requests

default_args = {
    'owner': 'kbo_admin',
    'depends_on_past': False,
    'start_date': datetime(2026, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

def trigger_batch_email_briefing():
    """
    FastAPI 백엔드의 /api/send-batch-email-briefings 엔드포인트를 호출합니다.
    """
    # Docker 컨테이너 내 네트워크 접속 주소 (kbo-backend 서비스)
    url = "http://backend:8000/api/send-batch-email-briefings"
    try:
        response = requests.post(url, timeout=60)
        response.raise_for_status()
        res_data = response.json()
        print(f"✅ Airflow 정기 이메일 브리핑 트리거 성공: {res_data}")
    except Exception as e:
        # 컨테이너 외부/로컬 실행 대비 localhost 폴백 시도
        try:
            fallback_url = "http://localhost:8000/api/send-batch-email-briefings"
            response = requests.post(fallback_url, timeout=60)
            response.raise_for_status()
            print(f"✅ Airflow 정기 이메일 브리핑 트리거 성공 (localhost): {response.json()}")
        except Exception as err:
            raise RuntimeError(f"❌ 정기 이메일 브리핑 트리거 실패: {err}")

with DAG(
    'kbo_daily_email_briefing_dag',
    default_args=default_args,
    description='매일 아침 8시 KBO 관심 구단 정기 구독 이메일 자동 발송',
    schedule_interval='0 8 * * *',  # 매일 오전 08:00 정각 스케줄
    catchup=False,
    is_paused_upon_creation=False,
    tags=['kbo', 'email', 'briefing', 'daily']
) as dag:

    send_daily_emails = PythonOperator(
        task_id='send_daily_email_briefings',
        python_callable=trigger_batch_email_briefing
    )
