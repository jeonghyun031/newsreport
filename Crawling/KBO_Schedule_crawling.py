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
    # 1. Chrome 브라우저 및 속도 최적화 옵션 설정 (Headless)
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('window-size=1920x1080')
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false") # 이미지 차단으로 파싱 속도 대폭 상승

    print("Chrome 브라우저를 백그라운드로 실행합니다...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    schedule_list = []

    try:
        # 2. 대상 연월 설정 (원하는 달을 입력하세요. 공백이면 이번 달 자동 설정)
        target_month = "202606" 
        if not target_month or not target_month.strip():
            target_month = datetime.now().strftime("%Y%m")
            
        base_url = "https://sports.daum.net/schedule/kbo"
        target_url = f"{base_url}?date={target_month}"
        print(f"목표 일정 페이지로 이동합니다: {target_url}")

        driver.get(target_url)
        time.sleep(3) # 동적 데이터(자바스크립트) 로딩 대기

        # 3. BeautifulSoup으로 HTML 파싱 시작
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # HTML 구조 내 모든 경기 행(tr) 탐색
        days_elements = soup.select(".tbl_schedule tbody tr") or soup.select("tr[data-date]")

        if not days_elements:
            print("경기 일정 데이터를 찾을 수 없습니다. 페이지 구조를 확인해 주세요.")
            return

        print(f"데이터 수집 및 정제 시작 (총 {len(days_elements)}개 경기 탐색)...")

        for row in days_elements:
            try:
                # [A] 태그 속성에서 정확한 날짜 데이터 가져오기 (예: "20260701")
                game_date = row.get('data-date')
                if not game_date:
                    date_el = row.select_one(".td_date")
                    if date_el:
                        game_date = date_el.text.strip().replace("\n", " ")
                    else:
                        continue # 날짜 정보가 전혀 없다면 패스

                # [B] 경기 시간 및 구장 추출
                time_el = row.select_one(".td_time")
                game_time = time_el.text.strip() if time_el else "미정"
                
                area_el = row.select_one(".td_area")
                stadium = " ".join(area_el.text.split()) if area_el else "구장 미정"

                # [C] 원정팀 및 홈팀 추출 (HTML 구조 특성에 맞춰 원정/홈 스왑 보정 완료)
                # HTML 구조 상 위에 배치된 클래스(team_home)가 실제 전광판의 원정팀(Away)입니다.
                away_box = row.select_one(".team_home")  
                home_box = row.select_one(".team_away")  
                
                if not home_box or not away_box:
                    continue # 팀 정보가 없는 특수 행은 패스
                
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
                
                # 경기가 종료된 경우 상태창에 스코어를 예쁘게 조합해 줍니다.
                if status == "종료" and home_score and away_score:
                    status_str = f"종료 ({away_team} {away_score} : {home_score} {home_team})"
                else:
                    status_str = status

                # 결과 딕셔너리 적재
                schedule_list.append({
                    "일자": game_date,
                    "시간": game_time,
                    "원정팀": away_team,
                    "홈팀": home_team,
                    "구장": stadium,
                    "상태": status_str
                })

            except Exception as e:
                # 개별 경기 오류 발생 시 전체가 멈추지 않고 넘어가도록 처리
                continue

        # 4. 판다스 DataFrame 변환 및 파일 저장
        df = pd.DataFrame(schedule_list)
        if not df.empty:
            current_time = datetime.now().strftime("%Y%m%d_%H%M")
            filename = f"KBO_Schedule({target_month})_{current_time}.csv"
            
            output_dir = "Crawling"
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            save_path = os.path.join(output_dir, filename)
            
            # 컬럼 정렬 및 저장 (필요 시 뉴스 크롤러처럼 header=False 설정 가능)
            df = df[["일자", "시간", "원정팀", "홈팀", "구장", "상태"]]
            df.to_csv(save_path, index=False, header=False, encoding="utf-8-sig")
            
            print(f"\n파일 저장 성공! -> {save_path} (총 {len(df)}건 완료)")
        else:
            print("데이터가 비어있어 저장에 실패했습니다.")

    except Exception as e:
        print(f"크롤링 중 치명적 에러 발생: {e}")
    finally:
        driver.quit()
        print("브라우저를 안전하게 종료했습니다.")

if __name__ == "__main__":
    main()