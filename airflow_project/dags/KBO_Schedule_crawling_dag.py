import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests

# Airflow 관련 오퍼레이터 및 패키지
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.exceptions import AirflowException

# 셀레니움 & 뷰티풀수프
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
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
    """[야구 - 통합 크롤러] 오늘 경기(결과) 및 내일 경기(선발투수) 통합 수집 및 예외 처리 방어 로직"""
    print("원격 셀레니움 브라우저를 구동합니다...")
    driver = get_remote_driver()
    integrated_list = []
    
    try:
        import pendulum
        
        # [1] 실제 실행 시점의 현재 한국 시간(KST) 기준 설정
        execution_now_kst = pendulum.now("Asia/Seoul")
        
        # 🎯 타깃 날짜 다각화 (오늘 + 내일 모두 수집)
        today_date_str = execution_now_kst.strftime("%Y%m%d")
        tomorrow_date_str = execution_now_kst.add(days=1).strftime("%Y%m%d")
        target_month = execution_now_kst.strftime("%Y%m") # 파일 저장용 당월 정보
        
        base_url = "https://sports.daum.net"
        schedule_url = f"{base_url}/schedule/kbo?date={target_month}"
        
        print(f"현재 실행 시간(KST): {execution_now_kst.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🎯 수집 대상 -> 오늘: {today_date_str} / 내일: {tomorrow_date_str}")
        print(f"목표 일정 페이지로 이동합니다: {schedule_url}")

        driver.get(schedule_url)
        time.sleep(4) 

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # 🌟 [방어] 오늘 경기 tr 행과 내일 경기 tr 행을 모두 선택
        target_rows = soup.select(f"tr[data-date='{today_date_str}'], tr[data-date='{tomorrow_date_str}']")

        if not target_rows:
            print(f"⚠️ {today_date_str} 및 {tomorrow_date_str} 날짜에 모두 해당하는 경기 일정이 없습니다. (우천 취소 또는 경기 없는 날)")
            context["ti"].xcom_push(key="crawl_count", value=0)
            return

        print(f"총 {len(target_rows)}개의 대상 경기를 발견했습니다. 분석을 시작합니다.")

        matches_to_crawl = []
        for row in target_rows:
            try:
                # 행 자체의 날짜 속성 추출 (오늘 경기인지 내일 경기인지 구분)
                row_date = row.get('data-date', '').strip()
                
                # 기본 정보 파싱
                time_el = row.select_one(".td_time")
                game_time = time_el.text.strip() if time_el else "미정"
                
                area_el = row.select_one(".td_area")
                stadium = " ".join(area_el.text.split()) if area_el else "구장 미정"

                away_box = row.select_one(".team_home")  
                home_box = row.select_one(".team_away")  
                if not home_box or not away_box:
                    continue
                
                away_team = away_box.select_one(".txt_team").text.strip()
                home_team = home_box.select_one(".txt_team").text.strip()
                
                status_el = row.select_one(".state_game")
                status_str = " ".join(status_el.text.split()) if status_el else "예정"

                # 상세 매치 센터 링크 추출
                link_el = row.select_one(".td_btn .link_game")
                detail_url = None
                if link_el and link_el.get('href'):
                    href = link_el.get('href')
                    detail_url = href if href.startswith("http") else f"{base_url}{href}"

                matches_to_crawl.append({
                    "game_date": row_date,
                    "game_time": game_time,
                    "away_team": away_team,
                    "home_team": home_team,
                    "stadium": stadium,
                    "status": status_str,
                    "detail_url": detail_url,
                    "away_pitcher": "미정", # 기본값 세팅으로 에러 방어
                    "home_pitcher": "미정"  # 기본값 세팅으로 에러 방어
                })
            except Exception as e:
                print(f"❌ 경기 일정 개별 행 파싱 오류(스킵): {e}")
                continue

        # [3] 매치 상세 페이지 순회 및 선발 투수 추출 (예외 방어 특화)
        for match in matches_to_crawl:
            is_finished = "종료" in match["status"]

            if not match["detail_url"]:
                print(f" └ ⚠️ [{match['away_team']} vs {match['home_team']}] 상세 링크 없음 -> 선발투수 '미정' 처리")
                integrated_list.append(match)
                continue
            
            try:
                print(f" └ 🔗 매치 상세 페이지 이동 중: {match['detail_url']} (상태: {match['status']})")
                driver.get(match["detail_url"])
                time.sleep(2.5) # 로딩 대기
                
                detail_soup = BeautifulSoup(driver.page_source, 'html.parser')
                
                # match_starter 영역 탐색
                starter_zone = detail_soup.select_one(".match_starter")
                if starter_zone:
                    away_p_el = starter_zone.select_one(".starter_vs1 .link_txt")
                    home_p_el = starter_zone.select_one(".starter_vs2 .link_txt")
                    
                    if away_p_el:
                        match["away_pitcher"] = away_p_el.text.strip()
                    if home_p_el:
                        match["home_pitcher"] = home_p_el.text.strip()
                        
                    print(f"   🎯 선발투수 추출 성공 -> 원정: {match['away_pitcher']} / 홈: {match['home_pitcher']}")
                else:
                    if is_finished:
                        print(f"   ℹ️ 종료된 경기입니다. 선발투수 영역 생략으로 인해 '미정' 처리 유지.")
                    else:
                        print(f"   ⚠️ 미시작 경기이나 선발투수 정보 미공개 상태입니다. '미정' 처리 유지.")
                        
            except Exception as e:
                print(f"   ❌ 상세 페이지 분석 중 에러 발생(미정 처리 후 패스): {e}")
            
            # 최종 안전 적재
            integrated_list.append({
                "game_date": match["game_date"],
                "game_time": match["game_time"],
                "away_team": match["away_team"],
                "home_team": match["home_team"],
                "stadium": match["stadium"],
                "status": match["status"],
                "away_pitcher": match["away_pitcher"],
                "home_pitcher": match["home_pitcher"]
            })

        # 데이터프레임 빌드 및 공유 디렉토리 내보내기
        df = pd.DataFrame(integrated_list)
        if df.empty:
            df = pd.DataFrame(columns=["game_date", "game_time", "away_team", "home_team", "stadium", "status", "away_pitcher", "home_pitcher"])

        columns_order = ["game_date", "game_time", "away_team", "home_team", "stadium", "status", "away_pitcher", "home_pitcher"]
        df = df[columns_order]
        
        filename = f"raw_kbo_schedule_{target_month}.csv"
        if not os.path.exists(SHARED_RAW_DIR):
            os.makedirs(SHARED_RAW_DIR, exist_ok=True) 
        save_path = os.path.join(SHARED_RAW_DIR, filename)

        # 🌟 헤더 없이 깔끔하게 저장 (Overwrite 모드 대응)
        df.to_csv(save_path, index=False, header=False, encoding="utf-8-sig")

        len_df = len(integrated_list)
        context["ti"].xcom_push(key="crawl_count", value=len_df)
        print(f"\n[통합 저장 완료] 오늘+내일 경기 정상 적재 완료 -> {save_path} (총 {len_df}건)")

    except Exception as e:
        print(f"🔥 크롤링 중 크리티컬 에러 발생: {e}")
        raise e
    finally:
        driver.quit()
        print("Chrome 브라우저를 안전하게 닫았습니다.")


# ==============================================================================
# [Slack 알림 및 콜백용 함수 구성]
# ==============================================================================
def slack_success_alert(message):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️ SLACK_WEBHOOK_URL 환경변수가 존재하지 않아 발송을 스킵합니다.")
        return
    requests.post(webhook_url, json={"text": message}, timeout=10)


def crawl_success_message(**context):
    schedule_count = context["ti"].xcom_pull(task_ids="crawl_kbo_schedule_task", key="crawl_count") or 0
    message = (
        "✅ *KBO 오늘+내일자 일정 및 선발투수 크롤링 완료*\n\n"
        "• *DAG* : `daum_kbo_schedule_spark_pipeline`\n"
        f"• *수집 및 동시저장된 데이터* : `{schedule_count}건` (상세 매치센터 타깃 완료)"
    )
    slack_success_alert(message)


def spark_success_message():
    message = (
        "✅ *Spark Schedule ETL 완료*\n\n"
        "• *DAG* : `daum_kbo_schedule_spark_pipeline`\n"
        "• *DB* : `AWS MySQL(kbo_schedule) 적재 정상 완료`"
    )
    slack_success_alert(message)


def on_failure_alert(context):
    dag_id = context["task_instance"].dag_id
    task_id = context["task_instance"].task_id
    logical_date = context["logical_date"].astimezone(KST_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
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
    slack_success_alert(message)


# ==============================================================================
# [DAG 본체 선언]
# ==============================================================================
default_args = {
    'owner': 'kbo_admin',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 15),
    'retries': 0,
    'retry_delay': timedelta(minutes=5),
    'on_failure_callback': on_failure_alert
}

with DAG(
    'daum_kbo_schedule_spark_pipeline',
    default_args=default_args,
    description='Daum KBO 경기 일정 및 선발투수 통합 크롤링 후 Spark 처리 파이프라인',
    schedule_interval='0 6 * * *',  # 매일 오후 3시 KST (영국 UTC 기준 06:00)
    catchup=False,
    is_paused_upon_creation=False,
    tags=['crawling', 'kbo', 'schedule', 'spark'],
) as dag:

    # 1. KBO 일정 + 선발투수 통합 크롤링 실행 타스크
    crawl_schedule_task = PythonOperator(
        task_id='crawl_kbo_schedule_task',
        python_callable=run_kbo_schedule_crawler,
        provide_context=True,
    )

    # 2. 크롤링 완료 Slack 알림 타스크
    crawl_success_task = PythonOperator(
        task_id="crawl_success_alert",
        python_callable=crawl_success_message,
        provide_context=True,
    )

    # 3. Spark ETL 컨테이너 원격 제어 실행 타스크
    spark_transform_task = BashOperator(
        task_id="spark_remote_transform_task",
        bash_command=(
            'docker exec spark-container '
            'spark-submit '
            '--master local[*] '
            '--packages com.mysql:mysql-connector-j:8.3.0 '
            '/home/jovyan/work/workspace/etl_schedule_job.py '
            '{{ ds_nodash[:6] }}' 
        ),
    )

    # 4. 전체 공정 최종 성공 알림 타스크
    spark_success_task = PythonOperator(
        task_id="spark_success_alert",
        python_callable=spark_success_message
    )

    # [의존성 흐름 정의]
    crawl_schedule_task >> crawl_success_task >> spark_transform_task >> spark_success_task