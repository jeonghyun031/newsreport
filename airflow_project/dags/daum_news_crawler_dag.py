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
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

# ==============================================================================
# [설정] 공유 볼륨 및 타임존 정의
# ==============================================================================
SHARED_VOLUME_DIR = "/opt/shared"
SHARED_RAW_DIR = os.path.join(SHARED_VOLUME_DIR, "raw")
KST_TIMEZONE = timezone(timedelta(hours=9))

def convert_relative_time(date_text):
    """상대 시간을 현재 시각 기준으로 'YYYY.MM.DD HH:MM' 형태로 변환합니다."""
    now = datetime.now(KST_TIMEZONE)
    date_text = date_text.strip()
    
    if '분 전' in date_text:
        minutes = int(re.findall(r'\d+', date_text)[0])
        converted_time = now - timedelta(minutes=minutes)
        return converted_time.strftime("%Y.%m.%d %H:%M")
    elif '시간 전' in date_text:
        hours = int(re.findall(r'\d+', date_text)[0])
        converted_time = now - timedelta(hours=hours)
        return converted_time.strftime("%Y.%m.%d %H:%M")
    elif '방금' in date_text or '초 전' in date_text:
        return now.strftime("%Y.%m.%d %H:%M")
    else:
        return date_text


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


def run_pure_crawler(**context):
    """[야구 - 속보] 최신 뉴스 크롤러 (더보기 루프 포함)"""
    print("원격 셀레니움 컨테이너에 브라우저 실행을 요청합니다...")
    driver = get_remote_driver()
    news_list = []
    
    try:
        import pendulum
        utc_logical_date = context['logical_date']
        kst_logical_date = utc_logical_date.in_timezone(pendulum.timezone("Asia/Seoul"))
        news_date = kst_logical_date.strftime("%Y%m%d")
        
        # 🌟 수집 대상을 속보(breaking) 페이지로 명확히 수정했습니다.
        target_url = "https://sports.daum.net/baseball/news/breaking"
        driver.get(target_url)
        time.sleep(2) 
        
        # --- 더보기 클릭 루프 ---
        click_count = 0
        last_news_count = 0 

        while True:
            try:
                current_soup = BeautifulSoup(driver.page_source, 'html.parser')
                ul_element = current_soup.select_one(".list_news")
                current_news_count = len(ul_element.select("li")) if ul_element else 0
                
                if click_count > 0 and current_news_count == last_news_count:
                    print("더 이상 추가되는 기사가 없습니다. 루프를 종료합니다.")
                    break
                
                last_news_count = current_news_count
                more_button = driver.find_element(By.CLASS_NAME, "link_moreview")
                
                if more_button.is_displayed():
                    more_button.click()
                    click_count += 1
                    print(f"더보기 버튼 {click_count}번째 클릭 완료 (현재 기사 수: {current_news_count}개)")
                    time.sleep(1.5) 
                else:
                    print("더보기 버튼이 시각적으로 숨겨졌습니다.")
                    break
                    
            except Exception:
                print("더보기 클릭 종료 또는 모든 기사 로드 완료")
                break

        # --- 파싱 ---
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        ul_element = soup.select_one(".list_news")
        if not ul_element:
            print("뉴스 리스트 영역을 찾을 수 없습니다.")
            return
            
        lis = ul_element.select("li")
        print(f"총 수집 대상 뉴스 기사 수: {len(lis)}개")

        for idx, li in enumerate(lis):
            try:
                title_el = li.select_one(".link_txt")
                doct_el = li.select_one(".link_desc")
                info_el = li.select_one(".info_news")
                
                if not title_el:
                    continue
                    
                for screen_out_tag in info_el.select(".screen_out") if info_el else []:
                    screen_out_tag.decompose()
                                    
                title = title_el.text.strip()
                doct = doct_el.text.strip() if doct_el else ""
                
                news_url = title_el.get("href", "").strip()
                if not news_url and doct_el:
                    news_url = doct_el.get("href", "").strip()
                if news_url and not news_url.startswith("http"):
                    news_url = "https://sports.daum.net" + news_url
                
                txt_infos = info_el.select(".txt_info") if info_el else []
                if len(txt_infos) >= 2:
                    raw_date = txt_infos[0].text.strip()
                    script = txt_infos[1].text.strip()
                elif len(txt_infos) == 1:
                    raw_date = txt_infos[0].text.strip()
                    script = "알 수 없음"
                else:
                    raw_date = "방금 전"
                    script = "알 수 없음"
                
                if not any(k in raw_date for k in ['.', ':', '전']) and any(k in script for k in ['.', ':', '전']):
                    raw_date, script = script, raw_date
                    
                rk_date = convert_relative_time(raw_date)
                doct_clean = " ".join(doct.split())
                
                news_list.append({
                    "date": rk_date,
                    "title": title,
                    "media": script,
                    "content": doct_clean,
                    "url": news_url
                })
            
            except Exception as e:
                print(f"{idx+1}번째 뉴스 데이터 추출 중 오류 발생 (스킵): {e}")
                continue

        df = pd.DataFrame(news_list)
        if not df.empty:
            df = df[["date", "title", "media", "content", "url"]]
            filename = f"raw_news_{news_date}.csv"
            
            if not os.path.exists(SHARED_RAW_DIR):
                os.makedirs(SHARED_RAW_DIR)
            save_path = os.path.join(SHARED_RAW_DIR, filename)
            
            df.to_csv(save_path, index=False, header=True, encoding="utf-8-sig")
            len_df = len(df)
            
            context["ti"].xcom_push(key="crawl_count", value=len_df)
            print(f"\n[속보 완료] 공유 볼륨 원본 저장 성공! -> {save_path} (총 {len_df}건)")
        else:
            context["ti"].xcom_push(key="crawl_count", value=0)
            print("수집된 속보 데이터가 없습니다.")

    except Exception as e:
        print(f"속보 크롤링 진행 중 치명적 에러 발생: {e}")
        raise e
    finally:
        driver.quit()
        print("Chrome 브라우저를 안전하게 종료했습니다.")


def run_baseball_ranking_crawler(**context):
    """[야구 - 랭킹] 인기 랭킹 뉴스 크롤러 (KBO 리그)"""
    print("원격 셀레니움 컨테이너에 브라우저 실행을 요청합니다 (랭킹)...")
    driver = get_remote_driver()
    news_list = []
    
    try:
        import pendulum
        utc_logical_date = context['logical_date']
        kst_logical_date = utc_logical_date.in_timezone(pendulum.timezone("Asia/Seoul"))
        news_date = kst_logical_date.strftime("%Y%m%d")
        
        target_url = f"https://sports.daum.net/kbo/news/ranking?date={news_date}"
        driver.get(target_url)
        time.sleep(2)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        ul_element = soup.select_one(".list_news")
        
        if ul_element:
            for idx, li in enumerate(ul_element.select("li")):
                try:
                    title_el = li.select_one(".link_txt")
                    doct_el = li.select_one(".link_desc")
                    info_el = li.select_one(".info_news")
                    rank_el = li.select_one(".num_rank")
                    if not title_el: continue
                    
                    for tag in info_el.select(".screen_out") if info_el else []:
                        tag.decompose()
                        
                    title = title_el.text.strip()
                    if len(title) > 40: 
                        title = title[:40] + "..."
                    
                    doct = doct_el.text.strip() if doct_el else ""
                    news_url = title_el.get("href", "").strip()
                    if news_url and not news_url.startswith("http"):
                        news_url = "https://sports.daum.net" + news_url
                        
                    txt_infos = info_el.select(".txt_info") if info_el else []
                    raw_date, script = "방금 전", "알 수 없음"
                    if len(txt_infos) >= 2:
                        raw_date, script = txt_infos[0].text.strip(), txt_infos[1].text.strip()
                        
                    if not any(k in raw_date for k in ['.', ':', '전']) and any(k in script for k in ['.', ':', '전']):
                        raw_date, script = script, raw_date
                        
                    rk_date = convert_relative_time(raw_date)
                    rank = rank_el.text.strip() if rank_el else str(idx + 1)
                    
                    news_list.append({
                        "rank": rank, "date": rk_date, "title": title, "media": script,
                        "content": " ".join(doct.split()), "url": news_url, "type": "ranking"
                    })
                except Exception as e:
                    continue

        df = pd.DataFrame(news_list)
        if not df.empty:
            if not os.path.exists(SHARED_RAW_DIR): 
                os.makedirs(SHARED_RAW_DIR)
            save_path = os.path.join(SHARED_RAW_DIR, f"raw_baseball_ranking_{news_date}.csv")
            df.to_csv(save_path, index=False, encoding="utf-8-sig")
            len_df = len(df)
            
            # 🌟 랭킹 뉴스 수집량도 XCom에 보관하도록 추가
            context["ti"].xcom_push(key="crawl_count", value=len_df)
            print(f"[야구 랭킹 완료] 저장 성공 -> {save_path} ({len_df}건)")
        else:
            context["ti"].xcom_push(key="crawl_count", value=0)
            print("수집된 랭킹 데이터가 없습니다.")
            
    except Exception as e:
        print(f"랭킹 크롤링 진행 중 에러 발생: {e}")
        raise e
    finally:
        driver.quit()


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
    # 🌟 두 태스크의 XCom 값을 각각 가져와서 통합 집계합니다.
    breaking_count = context["ti"].xcom_pull(
        task_ids="run_pure_crawler_task",
        key="crawl_count"
    ) or 0

    ranking_count = context["ti"].xcom_pull(
        task_ids="crawl_baseball_ranking_task",
        key="crawl_count"
    ) or 0

    total_count = breaking_count + ranking_count

    message = (
        "✅ *크롤링 완료 안내*\n\n"
        "• *DAG* : `daum_baseball_crawling_spark_pipeline`\n"
        f"• *수집된 야구 속보 뉴스* : `{breaking_count}건`\n"
        f"• *수집된 KBO 랭킹 뉴스* : `{ranking_count}건`\n"
        f"• *총합 뉴스 수집량* : `{total_count}건`"
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
    # 🌟 원하지 않는 앞쪽 공백(Indent) 현상 제거
    message = (
        "✅ *Spark ETL 완료*\n\n"
        "• *DAG* : `daum_baseball_crawling_spark_pipeline`\n"
        "• *DB* : `MySQL 적재 정상 완료`"
    )
    slack_success_alert(message)


default_args = {
    'owner': 'COMSW',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 15),
    'retries': 0,
    'retry_delay': timedelta(minutes=5),
    'on_failure_callback': on_failure_alert
}

with DAG(
    'daum_baseball_crawling_spark_pipeline',
    default_args=default_args,
    description='Daum 야구 뉴스 크롤링 후 분산 Spark 컨테이너 전처리 파이프라인',
    schedule_interval='0 * * * *', # 한국 시간 기준 매시간 정각 실행
    catchup=False,
    tags=['crawling', 'spark', 'docker'],
) as dag:

    # 1. 야구 속보 크롤링 (Airflow 실행)
    crawl_task = PythonOperator(
        task_id='run_pure_crawler_task',
        python_callable=run_pure_crawler,
        provide_context=True,
    )
    
    # 2. 야구 랭킹 크롤링 (KBO 리그 인기 기사 1~20위)
    crawl_ranking = PythonOperator(
        task_id='crawl_baseball_ranking_task',
        python_callable=run_baseball_ranking_crawler,
        provide_context=True,
    )
    
    # 3. 크롤링 성공 알림
    crawl_success_task = PythonOperator(
        task_id="crawl_success_alert",
        python_callable=crawl_success_message,
        provide_context=True,
    )
    
    # 4. Spark 컨테이너 원격 실행 및 처리
    spark_transform_task = BashOperator(
        task_id="spark_remote_transform_task",
        bash_command=(
            'docker exec spark-container '
            'spark-submit '
            '--master local[*] '
            '--packages com.mysql:mysql-connector-j:8.3.0 '
            '/home/jovyan/work/workspace/etl_job.py '
            '{{ ds_nodash }}'
        ),
    )

    # 5. Spark 성공 알림
    spark_success_task = PythonOperator(
        task_id="spark_success_alert",
        python_callable=spark_success_message
    )

    # [수집 흐름 제어]
    # 병렬로 동작하는 두 수집 task([crawl_task, crawl_ranking])가 모두 정상 완료되어야 
    # crawl_success_task 단계로 넘어가 총합 알림을 보내고 Spark 작업을 시작합니다.
    [crawl_task, crawl_ranking] >> crawl_success_task >> spark_transform_task >> spark_success_task