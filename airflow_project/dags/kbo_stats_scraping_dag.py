import os
from datetime import datetime, timedelta
import requests

# Airflow 코어 오퍼레이터
from airflow import DAG
from airflow.operators.python import PythonOperator

# 작성한 크롤러 모듈에서 메인 함수 임포트
# (kbo_crawler.py 파일이 같은 dags 폴더 혹은 파이썬 패스에 있어야 합니다)
from kbo_crawler import main as run_kbo_crawler

# ==============================================================================
# [설정] 글로벌 변수 및 타임존 정의
# ==============================================================================
KST_TIMEZONE = timedelta(hours=9)

def on_failure_alert(context):
    """파이프라인 실패 시 슬랙 알림을 전송하는 콜백 함수"""
    dag_id = context["task_instance"].dag_id
    task_id = context["task_instance"].task_id
    
    # 실행 시간을 한국 시간(KST)으로 변경
    logical_date = (
        context["logical_date"]
        .astimezone(datetime.now().astimezone().tzinfo) # 컨테이너 시간 기준 변환
        .strftime("%Y-%m-%d %H:%M:%S")
    )
    exception = context.get("exception")
    log_url = context["task_instance"].log_url

    message = (
        "🚨 *[Airflow] KBO 스탯 수집 파이프라인 실패 알림*\n"
        f"• *DAG* : `{dag_id}`\n"
        f"• *Task* : `{task_id}`\n"
        f"• *실행시간* : `{logical_date}` (KST)\n"
        f"• *Error 로그 요약* : ```{exception}```\n"
        f"• *상세 로그 확인* : <{log_url}|[바로가기]>"
    )

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️ SLACK_WEBHOOK_URL 환경변수가 설정되지 않아 알림을 보낼 수 없습니다.")
        return
    
    try:
        response = requests.post(
            webhook_url,
            json={"text": message},
            timeout=10
        )
        response.raise_for_status()
        print("✅ Slack 에러 알림 전송 성공")
    except requests.exceptions.RequestException as e:
        print(f"❌ Slack 에러 알림 전송 실패: {e}")


# ==============================================================================
# [기본 구성] DAG Arguments 세팅
# ==============================================================================
default_args = {
    'owner': 'kbo_admin',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 15), # 2026년 시즌 서빙 환경
    'retries': 1,                        # 네트워크 타임아웃 대비 1회 재시도 설정
    'retry_delay': timedelta(minutes=10), # 재시도 전 10분 대기
    'on_failure_callback': on_failure_alert # 실패 시 슬랙 트리거
}

# ==============================================================================
# [DAG 정의] 매일 새벽에 KBO 공식 기록을 동기화하도록 스케줄링
# ==============================================================================
with DAG(
    'kbo_official_stats_sync_pipeline',
    default_args=default_args,
    description='KBO 공식 홈페이지 역대 투수/타자 스탯 크롤링 및 AWS RDS 적재 파이프ライン',
    schedule_interval='0 18 * * *', # 매일 한국 시간(KST) 새벽 3시 실행 (UTC 기준 18:00)
    catchup=False,
    is_paused_upon_creation=False,
    tags=['kbo', 'playwright', 'scraping', 'rds'],
) as dag:

    # 1. KBO 공식 기록실 크롤링 및 수집 실행 태스크
    kbo_scraping_task = PythonOperator(
        task_id='run_kbo_playwright_crawler',
        python_callable=run_kbo_crawler,
        provide_context=True,
        execution_timeout=timedelta(minutes=45) # 연도별 전수 조사는 시간이 걸리므로 제한시간 45분 넉넉히 인가
    )

    # 단일 태스크 파이프라인 흐름 정의
    kbo_scraping_task