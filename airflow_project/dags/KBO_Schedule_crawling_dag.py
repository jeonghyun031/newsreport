import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests

# Airflow 관련 오퍼레이터
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.exceptions import AirflowException

# 셀레니움 & 뷰티풀수프
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# ==============================================================================
# [설정] 공유 볼륨 및 타임존 정의
# ==============================================================================
SHARED_VOLUME_DIR = "/opt/shared"
SHARED_RAW_DIR = os.path.join(SHARED_VOLUME_DIR, "raw")
KST_TIMEZONE = timezone(timedelta(hours=9))


def get_remote_driver():
    """공통 원격 셀레니움 드라이버 생성 함수"""
    chrome_options = Options()
    chrome_options.add_argument('--headless') 
    chrome_options.add_argument('--no-sandbox') 
    chrome_options.add_argument('--disable-dev-shm-usage') 
    chrome_options.add_argument('window-size=1920x1080') 
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false") 
    
    return webdriver.Remote(
        command_executor='http://selenium-chrome:4444/wd/hub',
        options=chrome_options
    )


def run_kbo_schedule_crawler(**context):
    """[야구 - 경기 일정] KBO 월별 경기 일정 크롤러"""
    print("원격 셀레니움 컨테이너에 브라우저 실행을 요청합니다 (KBO 경기 일정)...")
    driver = get_remote_driver()
    schedule_list = []
    
    try:
        import pendulum
        utc_logical_date = context['logical_date']
        kst_logical_date = utc_logical_date.in_timezone(pendulum.timezone("Asia/Seoul"))
        target_month = kst_logical_date.strftime("%Y%m") # 예: 202607
        
        base_url = "https://sports.daum.net/schedule/kbo"
        target_url = f"{base_url}?date={target_month}"
        print(f"목표 일정 페이지로 이동합니다: {target_url}")

        driver.get(target_url)
        time.sleep(3) # 동적 자바스크립트 로딩 대기

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # HTML 구조 내 모든 경기 행(tr) 탐색
        days_elements = soup.select(".tbl_schedule tbody tr") or soup.select("tr[data-date]")

        if not days_elements:
            print("경기 일정 데이터를 찾을 수 없습니다. 페이지 구조를 확인해 주세요.")
            context["ti"].xcom_push(key="crawl_count", value=0)
            return

        print(f"데이터 수집 및 정제 시작 (총 {len(days_elements)}개 경기 탐색)...")

        for row in days_elements:
            try:
                # [A] 날짜 데이터 가져오기 (예: "20260701")
                game_date = row.get('data-date')
                if not game_date:
                    date_el = row.select_one(".td_date")
                    if date_el:
                        game_date = date_el.text.strip().replace("\n", " ")
                    else:
                        continue

                # [B] 경기 시간 및 구장 추출
                time_el = row.select_one(".td_time")
                game_time = time_el.text.strip() if time_el else "미정"
                
                area_el = row.select_one(".td_area")
                stadium = " ".join(area_el.text.split()) if area_el else "구장 미정"

                # [C] 원정팀 및 홈팀 추출
                away_box = row.select_one(".team_home")  
                home_box = row.select_one(".team_away")  
                
                if not home_box or not away_box:
                    continue
                
                away_team = away_box.select_one(".txt_team").text.strip()
                home_team = home_box.select_one(".txt_team").text.strip()
                
                # [D] 경기 결과 점수 추출
                away_score_el = away_box.select_one(".num_score")
                home_score_el = home_box.select_one(".num_score")
                away_score = away_score_el.text.strip() if away_score_el else ""
                home_score = home_score_el.text.strip() if home_score_el else ""

                # [E] 경기 상태 추출 (종료, 예정, 우천취소 등)
                status_el = row.select_one(".state_game")
                status = " ".join(status_el.text.split()) if status_el else "예정"
                
                if status == "종료" and home_score and away_score:
                    status_str = f"종료 ({away_team} {away_score} : {home_score} {home_team})"
                else:
                    status_str = status

                schedule_list.append({
                    "game_date": game_date,
                    "game_time": game_time,
                    "away_team": away_team,
                    "home_team": home_team,
                    "stadium": stadium,
                    "status": status_str
                })

            except Exception as e:
                print(f"개별 경기 일정 파싱 오류 (스킵): {e}")
                continue

        df = pd.DataFrame(schedule_list)
        
        if df.empty:
            df = pd.DataFrame(columns=["game_date", "game_time", "away_team", "home_team", "stadium", "status"])
            print("⚠️ 수집된 경기 일정 데이터가 없어 빈 스키마 구조로 설정합니다.")

        if not os.path.exists(SHARED_RAW_DIR):
            os.makedirs(SHARED_RAW_DIR)
            
        filename = f"raw_kbo_schedule_{target_month}.csv"
        save_path = os.path.join(SHARED_RAW_DIR, filename)
        
        df = df[["game_date", "game_time", "away_team", "home_team", "stadium", "status"]]
        df.to_csv(save_path, index=False, header=True, encoding="utf-8-sig")
        
        len_df = len(schedule_list)
        context["ti"].xcom_push(key="crawl_count", value=len_df)
        print(f"\n[경기 일정 완료] 공유 볼륨 저장 성공! -> {save_path} (총 {len_df}건 완료)")

    except Exception as e:
        print(f"경기 일정 크롤링 중 치명적 에러 발생: {e}")
        raise e
    finally:
        driver.quit()
        print("Chrome 브라우저(경기 일정)를 안전하게 종료했습니다.")


# ==============================================================================
# [Slack 알림 함수들]
# ==============================================================================
def slack_success_alert(message):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️ SLACK_WEBHOOK_URL 환경변수가 존재하지 않아 발송을 스킵합니다.")
        return

    requests.post(
        webhook_url,
        json={"text": message},
        timeout=10
    )


def crawl_success_message(**context):
    schedule_count = context["ti"].xcom_pull(
        task_ids="crawl_kbo_schedule_task",
        key="crawl_count"
    ) or 0

    message = (
        "✅ *KBO 경기 일정 크롤링 완료*\n\n"
        "• *DAG* : `daum_kbo_schedule_spark_pipeline`\n"
        f"• *수집된 일정 데이터* : `{schedule_count}건`"
    )
    slack_success_alert(message)


def on_failure_alert(context):
    dag_id = context["task_instance"].dag_id
    task_id = context["task_instance"].task_id
    logical_date = (
        context["logical_date"]
        .astimezone(KST_TIMEZONE)
        .strftime("%Y-%m-%d %H:%M:%S")
    )
    exception = context.get("exception")
    log_url = context["task_instance"].log_url

    message = (
        "=====*Airflow 파이프라인 실패 알림*=====\n"
        f"• DAG : `{dag_id}`\n"
        f"• Task : `{task_id}`\n"
        f"• 실행시간 : `{logical_date}` (KST)\n"
        f"• Error : ```{exception}```\n"
        f"• 로그 : {log_url}"
    )

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        raise ValueError("SLACK_WEBHOOK_URL 환경변수가 설정되지 않았습니다.")
    
    try:
        response = requests.post(
            webhook_url,
            json={"text": message},
            timeout=10
        )
        response.raise_for_status()
        print("✅ Slack 알림 전송 성공")
    except requests.exceptions.RequestException as e:
        print(f"❌ Slack 알림 전송 실패: {e}")


def spark_success_message():
    message = (
        "✅ *Spark Schedule ETL 완료*\n\n"
        "• *DAG* : `daum_kbo_schedule_spark_pipeline`\n"
        "• *DB* : `MySQL 적재 정상 완료`"
    )
    slack_success_alert(message)


# ==============================================================================
# [DAG 정의]
# ==============================================================================
default_args = {
    'owner': 'COMSW',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 15),
    'retries': 0,
    'retry_delay': timedelta(minutes=5),
    'on_failure_callback': on_failure_alert
}

with DAG(
    'daum_kbo_schedule_spark_pipeline',
    default_args=default_args,
    description='Daum KBO 경기 일정 크롤링 후 Spark 컨테이너 처리 파이프라인',
    schedule_interval='0 0 1 * *',  # 매월 1일 00:00 (UTC) 실행 -> 매월 1일 09:00 (KST)
    catchup=False,
    tags=['crawling', 'kbo', 'schedule', 'spark'],
) as dag:

    # 1. KBO 일정 크롤링
    crawl_schedule_task = PythonOperator(
        task_id='crawl_kbo_schedule_task',
        python_callable=run_kbo_schedule_crawler,
        provide_context=True,
    )

    # 2. 크롤링 완료 알림
    crawl_success_task = PythonOperator(
        task_id="crawl_success_alert",
        python_callable=crawl_success_message,
        provide_context=True,
    )

    # 3. Spark ETL 연동
    spark_transform_task = BashOperator(
        task_id="spark_remote_transform_task",
        bash_command=(
            'docker exec spark-container '
            'spark-submit '
            '--master local[*] '
            '--packages com.mysql:mysql-connector-j:8.3.0 '
            '/home/jovyan/work/workspace/etl_schedule_job.py '
            '{{ ds_nodash[:6] }}' # YYYYMM 형식 전달
        ),
    )

    # 4. Spark 완료 알림
    spark_success_task = PythonOperator(
        task_id="spark_success_alert",
        python_callable=spark_success_message
    )

    # [의존성 흐름 설정]
    crawl_schedule_task >> crawl_success_task >> spark_transform_task >> spark_success_task