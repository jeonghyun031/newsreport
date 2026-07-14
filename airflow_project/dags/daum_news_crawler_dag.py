import os
import re
import time
from datetime import datetime, timedelta
import pandas as pd

# Airflow 관련 오퍼레이터
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

# 셀레니움 & 뷰티풀수프
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
#from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

# ==============================================================================
# [중요 설정] 두 컨테이너가 공유하는 도커 볼륨 내부의 절대 경로를 정의합니다.
# ==============================================================================
SHARED_VOLUME_DIR = "/opt/shared"  # 도커 컴포즈에서 마운트한 공유 폴더 경로
SHARED_RAW_DIR = os.path.join(SHARED_VOLUME_DIR, "raw")
# ==============================================================================


def convert_relative_time(date_text):
    """
    상대 시간을 현재 시각 기준으로 'YYYY.MM.DD HH:MM' 형태로 변환합니다.
    """
    now = datetime.now()
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


def run_pure_crawler():
    """
    지휘관(Airflow) 컨테이너에서 무거운 브라우저를 띄워 
    공유 볼륨의 'raw' 폴더에 순수 원본 CSV를 적재하는 단계입니다.
    """
    chrome_options = Options()
    chrome_options.add_argument('--headless') 
    chrome_options.add_argument('--no-sandbox') 
    chrome_options.add_argument('--disable-dev-shm-usage') 
    chrome_options.add_argument('window-size=1920x1080') 
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false") 
    
    #print("Chrome 브라우저를 백그라운드에서 실행합니다...")
    print("원격 셀레니움 컨테이너에 브라우저 실행을 요청합니다...")
    #service = Service(ChromeDriverManager().install())
    #driver = webdriver.Chrome(service=service, options=chrome_options)
    driver = webdriver.Remote(
        command_executor='http://selenium-chrome:4444/wd/hub',
        options=chrome_options
    )
    news_list = []
    
    try:
        news_date = datetime.now().strftime("%Y%m%d")
        base_url = "https://sports.daum.net/baseball/news/breaking"
        target_url = base_url

        driver.get(target_url)
        time.sleep(2) 
        
        # --- 더보기 버튼 클릭 루프 ---
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
                print(f"더보기 클릭 종료 또는 모든 기사 로드 완료")
                break

        # --- 최종 HTML 파싱 (BeautifulSoup 기반) ---
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        ul_element = soup.select_one(".list_news")
        if not ul_element:
            print("뉴스 리스트 영역을 찾을 수 없습니다.")
            return
            
        lis = ul_element.select("li")
        print(f"총 수집 대상 뉴스 기사 수: {len(lis)}개")

        # --- 크롤링 루프 시작 ---
        for idx, li in enumerate(lis):
            try:
                title_el = li.select_one(".link_txt")
                doct_el = li.select_one(".link_desc")
                info_el = li.select_one(".info_news")
                
                if not title_el:
                    continue
                    
                for screen_out_tag in info_el.select(".screen_out"):
                    screen_out_tag.decompose()
                                    
                title = title_el.text.strip()
                doct = doct_el.text.strip() if doct_el else ""
                
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

                # 💡 [구조 변경]: 글자수 제한(40자) 및 공백 제거 등의 '데이터 가공/수정' 작업은
                # 이 파트에서 제외하고 원본 그대로 담은 후, 뒷단 단계인 Spark 컨테이너에 위임합니다.
                
                news_list.append({
                    "날짜": rk_date,
                    "제목": title,
                    "언론사": script,
                    "내용": doct
                })
            
            except Exception as e:
                print(f"{idx+1}번째 뉴스 데이터 추출 중 오류 발생 (스킵): {e}")
                continue

        # DataFrame 생성 및 공유 볼륨에 원본 저장
        df = pd.DataFrame(news_list)
        if not df.empty:
            df = df[["날짜", "제목", "언론사", "내용"]]
            df.columns = ["date", "title", "media", "content"] # Spark 처리가 편하게 영문명 권장

            filename = f"raw_news_{news_date}.csv"
            if not os.path.exists(SHARED_RAW_DIR):
                os.makedirs(SHARED_RAW_DIR)
            save_path = os.path.join(SHARED_RAW_DIR, filename)
            
            # 후속 Spark 처리를 위해 인덱스와 헤더를 포함하여 저장
            df.to_csv(save_path, index=False, header=True, encoding="utf-8-sig")
            print(f"\n[1단계 완료] 공유 볼륨 원본 저장 성공! -> {save_path}")
        else:
            print("수집된 데이터가 없습니다.")

    except Exception as e:
        print(f"크롤링 진행 중 치명적 에러 발생: {e}")
        raise e
    finally:
        driver.quit()
        print("Chrome 브라우저를 안전하게 종료했습니다.")


# --- Airflow DAG 스케줄 설정 ---
default_args = {
    'owner': 'COMSW',
    'depends_on_past': False,
    'start_date': datetime(2026, 1, 1),
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'daum_baseball_distributed_pipeline',
    default_args=default_args,
    description='Daum 야구 뉴스 크롤링 후 분산 Spark 컨테이너 전처리 파이프라인',
    schedule_interval='0 9 * * *',
    catchup=False,
    tags=['crawling', 'spark', 'docker'],
) as dag:

    # [Task 1]: 크롤링 수행 태스크 (Airflow 실행)
    crawl_task = PythonOperator(
        task_id='run_pure_crawler_task',
        python_callable=run_pure_crawler,
    )

    # [Task 2]: Spark 컨테이너 원격 실행 태스크
    # Airflow 웹 UI 연결 정보(Connections)에 등록된 'spark_default' 주소로 전처리 명령을 보냅니다.
    spark_transform_task = SparkSubmitOperator(
        task_id='spark_remote_transform_task',
        application=os.path.join(SHARED_VOLUME_DIR, 'scripts/spark_process.py'),  # 공유 폴더 내 Spark 실행용 스크립트 위치
        conn_id='spark_default',
        verbose=True
    )

    # 순서 제어: 크롤링이 완벽히 끝나서 CSV가 만들어지면 Spark를 깨웁니다.
    crawl_task >> spark_transform_task
    