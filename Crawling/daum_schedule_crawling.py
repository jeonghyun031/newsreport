# --- 모듈 설치 명령어 (기존 환경 유지) ---
# pip install pandas selenium webdriver-manager beautifulsoup4
import os
import time
from datetime import datetime
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

def main():
    # 1. Chrome Headless 옵션 설정
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('window-size=1920x1080')
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false") # 이미지 차단으로 속도 최적화

    print("Chrome 브라우저를 실행합니다...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    schedule_list = []

    try:
        # 2. 대상 URL 설정 (공백이면 이번 달, 값이 있으면 해당 연월)
        target_month = "202607" 
        if not target_month.strip():
            target_month = datetime.now().strftime("%Y%m")
            
        base_url = "https://sports.daum.net/schedule/kbo"
        target_url = f"{base_url}?date={target_month}"
        print(f"목표 일정 페이지로 이동합니다: {target_url}")

        driver.get(target_url)
        time.sleep(3) # 동적 자바스크립트 로딩 대기

        # 3. BeautifulSoup 파싱
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # 다음 스포츠 일정 테이블의 주요 감싸는 영역 선택 (기본 구조 기준)
        schedule_box = soup.select_one(".box_schedule") or soup.select_one("#tblSchedule") or soup.select_one(".page_schedule")
        
        # 만약 전체 영역 클래스가 안 잡힐 경우 테이블 행(tr) 또는 일정 리스트 단위로 타겟팅
        # 다음 스케줄 페이지는 보통 일자별 레이아웃(.tbl_schedule 또는 .list_schedule)을 가집니다.
        days_elements = soup.select(".tbl_schedule tbody tr") or soup.select(".list_schedule li")
        
        if not days_elements:
            # 대안 탭 구조 파싱 (클래스명이 다를 경우를 대비한 방어 코드)
            days_elements = soup.find_all("tr")

        print(f"데이터 파싱 시작 (탐색된 행 수: {len(days_elements)}개)...")

        current_date = ""  # 날짜 셀이 병합되어 첫 행에만 있는 경우를 위한 변수

        for row in days_elements:
            try:
                # 4. 데이터 요소 추출 (다음 스케줄 특성 반영)
                # 날짜 열 획득
                date_el = row.select_one(".td_date") or row.select_one(".txt_date")
                if date_el:
                    current_date = date_el.text.strip().replace("\n", " ")
                
                # 시간, 팀, 구장 정보 획득
                time_el = row.select_one(".td_time") or row.select_one(".txt_time")
                team_left = row.select_one(".team_left") or row.select_one(".txt_team:nth-of-type(1)")
                team_right = row.select_one(".team_right") or row.select_one(".txt_team:nth-of-type(2)")
                stadium_el = row.select_one(".td_stadium") or row.select_one(".txt_stadium")
                status_el = row.select_one(".td_status") or row.select_one(".txt_status") # 경기결과 또는 취소여부
                
                # 필수 정보(팀 정보)가 없으면 일정 행이 아니므로 패스
                if not team_left or not team_right:
                    continue

                game_time = time_el.text.strip() if time_el else "미정"
                away_team = team_left.text.strip()
                home_team = team_right.text.strip()
                stadium = stadium_el.text.strip() if stadium_el else "구장 미정"
                status = status_el.text.strip() if status_el else "예정"

                # 공백 문자 정돈
                away_team = " ".join(away_team.split())
                home_team = " ".join(home_team.split())

                # 콘솔 출력 확인용
                # print(f"[{current_date}] {game_time} | {away_team} vs {home_team} ({stadium}) - {status}")

                schedule_list.append({
                    "일자": current_date,
                    "시간": game_time,
                    "원정팀": away_team,
                    "홈팀": home_team,
                    "구장": stadium,
                    "상태": status
                })

            except Exception as e:
                continue

        # 5. DataFrame 생성 및 CSV 저장 (헤더 제외 옵션 반영 가능)
        df = pd.DataFrame(schedule_list)
        if not df.empty:
            current_time = datetime.now().strftime("%Y%m%d_%H%M")
            filename = f"KBO_Schedule_{target_month}_{current_time}.csv"
            
            output_dir = "Crawling"
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            save_path = os.path.join(output_dir, filename)
            
            # 뉴스 크롤러와 통일성 유지 (컬럼 순서 명시 및 저장)
            df = df[["일자", "시간", "원정팀", "홈팀", "구장", "상태"]]
            
            # 필요에 따라 첫 열(헤더)을 지우고 싶다면 header=False 추가 가능
            df.to_csv(save_path, index=False, header=True, encoding="utf-8-sig")
            print(f"\n일정 저장 성공! -> {save_path} (총 {len(df)}건 수집 완료)")
        else:
            print("수집된 KBO 일정 데이터가 없습니다. 페이지 클래스 구조를 재점검해보세요.")

    except Exception as e:
        print(f"일정 크롤링 중 치명적 오류 발생: {e}")
    finally:
        driver.quit()
        print("브라우저를 안전하게 종료했습니다.")

if __name__ == "__main__":
    main()