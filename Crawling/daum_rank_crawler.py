

# ---모듈에러날때 install 명령어---
# /c/Users/COMSW/.local/bin/python3.14.exe -m pip install pandas selenium webdriver-manager requests --break-system-packages
# ---실행 코드---
# /c/Users/COMSW/.local/bin/python3.14.exe Crawling/daum_crawler.py
import re
import time
import os
import textwrap
from datetime import datetime, timedelta, timezone
kst = timezone(timedelta(hours=9))
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def convert_relative_time(date_text):
    """
    '23분 전', '3시간 전', '방금 전' 등의 상대 시간을
    현재 시각 기준으로 'YYYY.MM.DD HH:MM' 형태로 변환합니다.
    """
    now = datetime.now(kst)
    date_text = date_text.strip()
    
    # 1. '분 전' 처리
    if '분 전' in date_text:
        minutes = int(re.findall(r'\d+', date_text)[0])
        converted_time = now - timedelta(minutes=minutes)
        return converted_time.strftime("%Y.%m.%d %H:%M")
        
    # 2. '시간 전' 처리
    elif '시간 전' in date_text:
        hours = int(re.findall(r'\d+', date_text)[0])
        converted_time = now - timedelta(hours=hours)
        return converted_time.strftime("%Y.%m.%d %H:%M")
        
    # 3. '방금 전' 또는 '초 전' 처리
    elif '방금' in date_text or '초 전' in date_text:
        return now.strftime("%Y.%m.%d %H:%M")
        
    # 4. 이미 정상적인 날짜 형태('2026.07.10 10:36')이거나 기타 변환 불가능한 경우 그대로 반환
    else:
        return date_text


def main():
    # 1. Chrome 옵션 설정 (Docker Headless 환경 필수)
    chrome_options = Options()
    chrome_options.add_argument('--headless')              # GUI 없이 실행
    chrome_options.add_argument('--no-sandbox')            # Docker 환경 권한 문제 방지
    chrome_options.add_argument('--disable-dev-shm-usage') # 메모리 부족 에러 방지
    chrome_options.add_argument('window-size=1920x1080')   # 가상 화면 크기 지정
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # 속도 최적화: 불필요한 이미지 로딩을 차단하여 수집 속도를 대폭 끌어올립니다.
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    
    print("Chrome 브라우저를 백그라운드에서 실행합니다...")

    # 2. WebDriver 실행 (webdriver-manager가 드라이버 자동 다운로드)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    # 요소를 찾을 때 대기시간
    driver.implicitly_wait(5)
    
    news_list = []

    try:
        # 설정할 날짜 (공백이면 기본 페이지, 값이 있으면 해당 날짜 페이지로 이동) ex)20260708
        news_date = "" 
        base_url = "https://sports.daum.net/worldsoccer/news/ranking"

        # news_date 비어있는지(공백인지) 확인하는 조건문
        if not news_date.strip():
            target_url = base_url
            print(f"날짜가 지정되지 않아 기본 랭킹 페이지로 이동합니다: {target_url}")
        else:
            target_url = f"{base_url}?date={news_date}"
            print(f"지정된 날짜({news_date}) 랭킹 페이지로 이동합니다: {target_url}")

        # 셀레니움 드라이버로 이동
        driver.get(target_url)

        # 뉴스 리스트 감싸는 ul 태그가 올 때까지 최대 10초 대기
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "list_news"))
        )
        
        # 뉴스 리스트 영역 추출
        main_element = driver.find_element(By.CLASS_NAME, "list_news")
        lis = main_element.find_elements(By.TAG_NAME, "li")

        # --- 크롤링 루프 시작 ---
        for idx, li in enumerate(lis):
            try:
                title = li.find_element(By.CLASS_NAME, "link_txt").text.strip()
                doct = li.find_element(By.CLASS_NAME, "link_desc").text.strip()
                rank = li.find_element(By.CLASS_NAME, "num_rank").text.strip()
                
                info = li.find_element(By.CLASS_NAME, "info_news")
                txt_infos = info.find_elements(By.CLASS_NAME, "txt_info")
                
                raw_date = txt_infos[0].text
                script = txt_infos[1].text
                
                # 정보 순서 예외 처리 스왑
                if '전' in script or any(chr.isdigit() for chr in script) and not ('전' in raw_date or any(chr.isdigit() for chr in raw_date)):
                    raw_date, script = script, raw_date
                    
                rk_date = convert_relative_time(raw_date)

                # 제목 축소 (40자 제한 후 '...' 결합)
                if len(title) > 40:
                    title = title[:40] + "..."

                # 가독성 정렬
                doct_clean = " ".join(doct.split())
            
                # 콘솔 터미널 출력 확인용
                print(f"[{rank}위] {title} ({script})")
                print(f"ㄴ 날짜: {rk_date}")
                print("=========================================================")

                # 원하시는 컬럼 순서 구성 (순위 -> 날짜 -> 제목 -> 언론사 -> 내용)
                news_list.append({
                    "순위": rank,
                    "날짜": rk_date,
                    "제목": title,
                    "언론사": script,
                    "내용": doct_clean
                })
            
            except Exception as e:
                # 🛡️ 루프 내 예외 처리: 특정 뉴스 한 개가 깨져도 크롤러 전체가 뻗지 않고 다음 뉴스로 패스
                print(f"{idx+1}번째 뉴스 데이터 추출 중 오류 발생 (스킵): {e}")
                continue

        # DataFrame 생성 및 컬럼 순서 명시적 고정
        df = pd.DataFrame(news_list)
        if not df.empty:
            df = df[["순위", "날짜", "제목", "언론사", "내용"]]
            
            # 파일명 현재 '연월일_시분' 추가
            current_time = datetime.now(kst).strftime("%Y%m%d_%H%M")
            filename = f"NewsRank_{news_date}_daum_{current_time}.csv"
            
            # Crawling 폴더 내부에 격리 저장
            output_dir = "Crawling"
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            save_path = os.path.join(output_dir, filename)
            
            # CSV 저장 (한글 깨짐 방지)
            df.to_csv(save_path, index=False, encoding="utf-8-sig")
            print(f"{save_path}저장 완료!")
        else:
            print("수집된 데이터가 없습니다.")

    except Exception as e:
        print(f"크롤링 진행 중 에러 발생: {e}")

    finally:
        # 백그라운드 크롬 프로세스가 좀비 메모리로 남지 않도록 완전히 안전 종료
        driver.quit()
        print("Chrome 브라우저를 안전하게 종료했습니다.")


if __name__ == "__main__":
    main()