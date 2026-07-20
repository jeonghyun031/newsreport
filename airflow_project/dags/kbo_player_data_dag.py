import os

import time

import random

import pandas as pd

import requests

from datetime import datetime, timezone, timedelta

from datetime import timezone, timedelta

KST_TIMEZONE = timezone(timedelta(hours=9))



from airflow import DAG

from airflow.operators.python import PythonOperator

from airflow.operators.bash import BashOperator



from selenium import webdriver

from selenium.webdriver.common.by import By

from selenium.webdriver.chrome.options import Options

from bs4 import BeautifulSoup



# [설정]

SHARED_VOLUME_DIR = "/opt/shared"

PLAYER_DATA_DIR = os.path.join(SHARED_VOLUME_DIR, "players")

KST_TIMEZONE = timezone(timedelta(hours=9))



def get_remote_driver():

    chrome_options = Options()

    chrome_options.add_argument('--headless')

    chrome_options.add_argument('--no-sandbox')

    chrome_options.add_argument('--disable-dev-shm-usage')

    return webdriver.Remote(command_executor='http://selenium-chrome:4444/wd/hub', options=chrome_options)



def random_sleep(min_sec=2, max_sec=5):

    time.sleep(random.uniform(min_sec, max_sec))



def crawl_hitter_data(**context):

    driver = get_remote_driver()

    base_url = "https://www.koreabaseball.com"

    os.makedirs(PLAYER_DATA_DIR, exist_ok=True)

    all_data = []

    current_page = 1

    

    try:

        driver.get(f"{base_url}/Record/Player/HitterBasic/Basic1.aspx")

        while True:

            soup = BeautifulSoup(driver.page_source, 'html.parser')

            players = soup.select('td a[href*="HitterDetail/Basic.aspx"]')

            for p in players:

                try:

                    player_id = p['href'].split('playerId=')[-1]

                    driver.get(f"{base_url}{p['href']}")

                    random_sleep(1, 2)

                    p_soup = BeautifulSoup(driver.page_source, 'html.parser')

                    name = p_soup.select_one('span[id*="lblName"]').text.strip()

                    birth = p_soup.select_one('span[id*="lblBirthday"]').text.strip()

                    driver.get(f"{base_url}/Record/Player/HitterDetail/Daily.aspx?playerId={player_id}")

                    random_sleep(1, 2)

                    d_soup = BeautifulSoup(driver.page_source, 'html.parser')

                    for tbl in d_soup.select('table.tbl'):

                        for row in tbl.select('tr'):

                            cols = row.select('td')

                            if len(cols) >= 18:

                                all_data.append({"name": name, "birth": birth, "date": cols[0].text.strip(), "opp": cols[1].text.strip(), "avg1": cols[2].text.strip(), "avg2": cols[17].text.strip(), "r": cols[5].text.strip(), "h": cols[6].text.strip(), "2b": cols[7].text.strip(), "hr": cols[9].text.strip(), "so": cols[15].text.strip(), "gdp": cols[16].text.strip()})

                except: continue

                driver.back(); driver.back()

            

            # 페이지 이동 로직 (자바스크립트 호출)

            try:

                next_page = current_page + 1

                driver.execute_script(f"__doPostBack('ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ucPager$btnNo{next_page}','')")

                current_page += 1

                random_sleep(3, 5)

            except: break

        

        df = pd.DataFrame(all_data)

        df.to_csv(f'{PLAYER_DATA_DIR}/hitter_stats_data.csv', index=False)

        context["ti"].xcom_push(key="crawl_count", value=len(df))

    finally:

        driver.quit()



def crawl_pitcher_data(**context):

    driver = get_remote_driver()

    base_url = "https://www.koreabaseball.com"

    os.makedirs(PLAYER_DATA_DIR, exist_ok=True)

    all_data = []

    current_page = 1

    

    try:

        driver.get(f"{base_url}/Record/Player/PitcherBasic/Basic1.aspx")

        while True:

            soup = BeautifulSoup(driver.page_source, 'html.parser')

            players = soup.select('td a[href*="PitcherDetail/Basic.aspx"]')

            for p in players:

                try:

                    player_id = p['href'].split('playerId=')[-1]

                    driver.get(f"{base_url}{p['href']}")

                    random_sleep(1, 2)

                    p_soup = BeautifulSoup(driver.page_source, 'html.parser')

                    name = p_soup.select_one('span[id*="lblName"]').text.strip()

                    birth = p_soup.select_one('span[id*="lblBirthday"]').text.strip()

                    driver.get(f"{base_url}/Record/Player/PitcherDetail/Daily.aspx?playerId={player_id}")

                    random_sleep(1, 2)

                    d_soup = BeautifulSoup(driver.page_source, 'html.parser')

                    for tbl in d_soup.select('table.tbl'):

                        for row in tbl.select('tr'):

                            cols = row.select('td')

                            if len(cols) >= 15:

                                all_data.append({"name": name, "birth": birth, "date": cols[0].text.strip(), "era1": cols[2].text.strip(), "h": cols[7].text.strip(), "hr": cols[9].text.strip(), "era2": cols[14].text.strip()})

                except: continue

                driver.back(); driver.back()

            

            try:

                next_page = current_page + 1

                driver.execute_script(f"__doPostBack('ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ucPager$btnNo{next_page}','')")

                current_page += 1

                random_sleep(3, 5)

            except: break

        

        df = pd.DataFrame(all_data)

        df.to_csv(f'{PLAYER_DATA_DIR}/pitcher_stats_data.csv', index=False)

        context["ti"].xcom_push(key="crawl_count", value=len(df))

    finally:

        driver.quit()



# [알림용 헬퍼 함수]
def slack_success_alert(message):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        requests.post(webhook_url, json={"text": message}, timeout=10)

def player_crawl_success_message(**context):
    hitter = context["ti"].xcom_pull(task_ids="crawl_hitter_task", key="crawl_count") or 0
    pitcher = context["ti"].xcom_pull(task_ids="crawl_pitcher_task", key="crawl_count") or 0
    message = (
        f"✅ *KBO 선수 데이터 수집 완료*\n"
        f"• *DAG* : `kbo_player_etl_pipeline`\n"
        f"• *수집 결과* : 타자 `{hitter}건`, 투수 `{pitcher}건`"
    )
    slack_success_alert(message)

def on_failure_alert(context):
    dag_id = context["task_instance"].dag_id
    task_id = context["task_instance"].task_id
    logical_date = context["logical_date"].astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
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

# [DAG 정의]
default_args = {
    'owner': 'kbo_admin',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 15),
    'retries': 0,
    'on_failure_callback': on_failure_alert # 실패 시 자동 호출
}

# [DAG 정의 - 중복 없이 단 하나만 존재]
default_args = {
    'owner': 'kbo_admin',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 15),
    'retries': 0,
    'on_failure_callback': on_failure_alert
}

with DAG(
    'kbo_player_etl_pipeline',
    default_args=default_args,
    schedule_interval='0 15 * * *',
    catchup=False,
    is_paused_upon_creation=False,
    tags=['crawling', 'kbo', 'player_data']
) as dag:

    crawl_hitter = PythonOperator(
        task_id='crawl_hitter_task', 
        python_callable=crawl_hitter_data, 
        provide_context=True
    )
    
    crawl_pitcher = PythonOperator(
        task_id='crawl_pitcher_task', 
        python_callable=crawl_pitcher_data, 
        provide_context=True
    )
    
    alert = PythonOperator(
        task_id="slack_alert", 
        python_callable=player_crawl_success_message, 
        provide_context=True
    )
    
    spark = BashOperator(
        task_id="spark_task", 
        bash_command='docker exec spark-container spark-submit /home/jovyan/work/workspace/etl_player_job.py'
    )
    
    [crawl_hitter, crawl_pitcher] >> alert >> spark