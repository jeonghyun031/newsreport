import os
import time
from datetime import datetime
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

def parse_kbo_table(table_box, mapping_dict):
    """
    종합/공격/수비 테이블 박스를 받아 데이터를 파싱하는 공통 함수
    """
    records = []
    if not table_box:
        return records
        
    rows = table_box.select("tbody tr")
    for row in rows:
        try:
            rank_el = row.select_one(".td_rank")
            name_el = row.select_one(".txt_name") or row.select_one(".td_name")
            
            if not rank_el or not name_el:
                continue
                
            row_data = {
                "순위": rank_el.text.strip(),
                "팀": name_el.text.strip()
            }
            
            fields = row.select("td[data-field]")
            for field in fields:
                field_name = field.get("data-field")
                field_value = field.text.strip()
                
                col_title = mapping_dict.get(field_name, field_name)
                row_data[col_title] = field_value
                
            records.append(row_data)
        except Exception:
            continue
    return records

def crawl_kbo_by_season(target_season=None):
    """
    target_season: "2024", "2025" 등의 문자열 또는 숫자형 연도.
                   공백('')이거나 값이 없으면(None) 현재 연도(2026)로 자동 지정.
    """
    # 1. 값이 공백이거나 없으면 현재 연도(2026)로 세팅
    if not target_season or str(target_season).strip() == "":
        current_year = datetime.now().year # 2026
        target_season = str(current_year)
    else:
        target_season = str(target_season).strip()

    # 2. 셀레니움 브라우저 기본 설정
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('window-size=1920x1080')
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")

    print(f"\n🚀 {target_season} 시즌 KBO 데이터 수집을 시작합니다...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        # 시즌 파라미터 주소 조합
        target_url = f"https://sports.daum.net/record/kbo/team?season={target_season}"
        print(f"🔗 접속 주소: {target_url}")
        driver.get(target_url)
        time.sleep(3)  # 로딩 대기

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # 데이터 매핑 사전 정의
        total_mapping = {
            "game": "경기", "win": "승", "draw": "무", "loss": "패", 
            "rank": "승률", "gb": "게임차", "streak": "연속"
        }
        
        attack_mapping = {
            "batAb": "타수", "batH": "안타", "bat2b": "2루타", "bat3b": "3루타",
            "batHr": "홈런", "batRbi": "타점", "batR": "득점", "batSb": "도루",
            "batBbhp": "사사구", "batSo": "삼진", "batGdp": "병살", "batAvg": "타율",
            "batObp": "출루율", "batSlg": "장타율", "batOps": "OPS"
        }
        
        defense_mapping = {
            "pitIp2": "이닝", "pitH": "피안타", "pitHr": "피홈런", "pitR": "실점",
            "pitEr": "자책", "pitBbhp": "사사구", "pitSo": "탈삼진", "pitEra": "평균자책",
            "fldErr": "실책", "pitWhip": "WHIP", "pitQs": "QS", "pitHld": "홀드", "pitSv": "세이브"
        }

        # [1] 종합 순위 영역 탐색
        total_box = soup.select_one("#recordList")
        total_data = parse_kbo_table(total_box, total_mapping)
        
        # [2] 공격 및 수비 순위 영역 탐색
        attack_data = []
        defense_data = []
        
        for box in soup.select(".box_record"):
            title_el = box.select_one(".tit_record")
            if not title_el:
                continue
                
            title_text = title_el.text.strip()
            if "공격 순위" in title_text:
                attack_data = parse_kbo_table(box, attack_mapping)
            elif "수비 순위" in title_text:
                defense_data = parse_kbo_table(box, defense_mapping)

        # 5. CSV 파일 저장 (파일명에 시즌 연도 명시)
        output_dir = "Crawling"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        print(f"\n================ [{target_season} 시즌 저장 결과] ================")
        
        if total_data:
            path = os.path.join(output_dir, f"KBO_Total_Record({target_season}).csv")
            pd.DataFrame(total_data).to_csv(path, index=False, encoding="utf-8-sig")
            print(f"✔ [종합 순위] 저장 완료 -> {path}")
            
        if attack_data:
            path = os.path.join(output_dir, f"KBO_Attack_Record({target_season}).csv")
            pd.DataFrame(attack_data).to_csv(path, index=False, encoding="utf-8-sig")
            print(f"✔ [공격 순위] 저장 완료 -> {path}")
            
        if defense_data:
            path = os.path.join(output_dir, f"KBO_Defense_Record({target_season}).csv")
            pd.DataFrame(defense_data).to_csv(path, index=False, encoding="utf-8-sig")
            print(f"✔ [수비 순위] 저장 완료 -> {path}")

    except Exception as e:
        print(f"❌ 크롤링 진행 중 오류 발생: {e}")
    finally:
        driver.quit()
        print(f"{target_season} 시즌 브라우저 종료.\n")

if __name__ == "__main__":
    # --- 테스트 가이드 ---
    
    # 예시 1: 특정 시즌을 넣었을 때 (예: 2024시즌)
    # crawl_kbo_by_season("2024")
    # crawl_kbo_by_season("2025")
    # 예시 2: 값을 공백문자('')나 무입력(None)으로 줬을 때 -> 현재 시즌인 2026으로 자동 변환되어 실행
    
    crawl_kbo_by_season("")