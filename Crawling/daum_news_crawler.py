# ---모듈에러날때 install 명령어---
# /c/Users/COMSW/.local/bin/python3.14.exe -m pip install pandas selenium webdriver-manager requests beautifulsoup4 --break-system-packages
# ---실행 코드---
# /c/Users/COMSW/.local/bin/python3.14.exe Crawling/daum_crawler.py
import re
import time
import os
from datetime import datetime, timedelta
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager  # 모듈명 수정
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup  # BeautifulSoup 누락 보완


def convert_relative_time(date_text):
    """
    '23분 전', '3시간 전', '방금 전' 등의 상대 시간을
    현재 시각 기준으로 'YYYY.MM.DD HH:MM' 형태로 변환합니다.
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


def main():
    # 1. Chrome 옵션 설정
    chrome_options = Options()
    chrome_options.add_argument('--headless')              # GUI 없이 실행
    chrome_options.add_argument('--no-sandbox')            # Docker 환경 권한 문제 방지
    chrome_options.add_argument('--disable-dev-shm-usage') # 메모리 부족 에러 방지
    chrome_options.add_argument('window-size=1920x1080')   # 가상 화면 크기 지정
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false") # 이미지 차단으로 속도 up
    
    print("Chrome 브라우저를 백그라운드에서 실행합니다...")

    # 2. WebDriver 실행
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    news_list = []
    
    try:
        news_date = "20260710" 
        base_url = "https://sports.daum.net/baseball/news/breaking"

        if not news_date.strip():
            news_date = datetime.now().strftime("%Y%m%d")
            target_url = base_url
            print(f"날짜가 지정되지 않아 기본 페이지로 이동합니다: {target_url}")
        else:
            target_url = f"{base_url}?date={news_date}&photo=false"
            print(f"지정된 날짜({news_date}) 페이지로 이동합니다: {target_url}")

        driver.get(target_url)
        time.sleep(2)  # 페이지 최초 로딩 대기
        
# --- [보완된] 더보기 버튼 클릭 루프 ---
        click_count = 0
        last_news_count = 0  # 이전 루프의 기사 개수를 저장할 변수

        while True:
            try:
                # 1. 현재 화면에 로드된 뉴스 기사 개수 파악
                current_soup = BeautifulSoup(driver.page_source, 'html.parser')
                ul_element = current_soup.select_one(".list_news")
                current_news_count = len(ul_element.select("li")) if ul_element else 0
                
                # 2. [핵심] 더보기를 눌렀는데도 기사 개수가 이전과 같다면 끝까지 온 것!
                if click_count > 0 and current_news_count == last_news_count:
                    print("더 이상 추가되는 기사가 없습니다. 루프를 종료합니다.")
                    break
                
                # 다음 비교를 위해 현재 개수를 저장
                last_news_count = current_news_count

                # 3. 더보기 버튼 찾기
                more_button = driver.find_element(By.CLASS_NAME, "link_moreview")
                
                # 4. 시각적으로 숨겨졌는지 한 번 더 체크하고 클릭
                if more_button.is_displayed():
                    more_button.click()
                    click_count += 1
                    print(f"더보기 버튼 {click_count}번째 클릭 완료 (현재 기사 수: {current_news_count}개)")
                    time.sleep(1.5)  # 웹 페이지가 데이터를 로드할 시간을 충분히 줌
                else:
                    print("더보기 버튼이 시각적으로 숨겨졌습니다.")
                    break
                    
            except Exception as e:
                # 더 이상 버튼이 없거나 에러가 나면 종료
                print(f"더보기 클릭 종료 또는 모든 기사 로드 완료")
                break

        # --- [핵심 수정] 끝까지 펼쳐진 최종 HTML을 딱 한 번만 파싱 ---
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # 'list_news' 클래스를 가진 ul 태그 안의 모든 li 태그 선택
        ul_element = soup.select_one(".list_news")
        if not ul_element:
            print("뉴스 리스트(list_news) 영역을 찾을 수 없습니다.")
            return
            
        lis = ul_element.select("li")
        print(f"총 수집 대상 뉴스 기사 수: {len(lis)}개")

        # --- 크롤링 루프 시작 (BeautifulSoup 기반으로 처리되어 속도가 압도적으로 빠름) ---
        for idx, li in enumerate(lis):
            try:
                # 요소 추출 (예외 처리를 위해 각 내부 변수 바인딩 시 체크)
                title_el = li.select_one(".link_txt")
                doct_el = li.select_one(".link_desc")
                info_el = li.select_one(".info_news")
                
                if not title_el:
                    continue
                    
                # info_news 영역 안에서 screen_out 클래스를 가진 태그를 찾아 아예 삭제(decompose)
                for screen_out_tag in info_el.select(".screen_out"):
                    screen_out_tag.decompose()
                                    
                title = title_el.text.strip()
                doct = doct_el.text.strip() if doct_el else ""
                
                # 언론사 및 시간 데이터 추출
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
                
                # # 정보 순서 예외 처리 스왑 (원본 로직 유지)
                # if '전' in script or any(chr.isdigit() for chr in script) and not ('전' in raw_date or any(chr.isdigit() for chr in raw_date)):
                #     raw_date, script = script, raw_date
                    
                # --- [교체할 안전 예외처리 코드] ---
                # 원래 날짜여야 할 raw_date에 날짜 기호가 없고, 
                # 언론사여야 할 script에 날짜 기호(., :, 전)가 들어있다면 그때만 뒤집습니다.
                if not any(k in raw_date for k in ['.', ':', '전']) and any(k in script for k in ['.', ':', '전']):
                    raw_date, script = script, raw_date
                # ------------------------------------
                    
                rk_date = convert_relative_time(raw_date)

                # 제목 40자 제한
                if len(title) > 40:
                    title = title[:40] + "..."

                # 가독성 정렬
                doct_clean = " ".join(doct.split())
            
                # 콘솔 출력 확인용
                # print(f"[{idx+1}] {title} ({script})")
                
                news_list.append({
                    "날짜": rk_date,
                    "제목": title,
                    "언론사": script,
                    "내용": doct_clean
                })
            
            except Exception as e:
                print(f"{idx+1}번째 뉴스 데이터 추출 중 오류 발생 (스킵): {e}")
                continue

        # DataFrame 생성 및 저장
        df = pd.DataFrame(news_list)
        if not df.empty:
            df = df[["날짜", "제목", "언론사", "내용"]] # 오타 방지 안전장치
            df.columns = ["날짜", "제목", "언론사", "내용"] # 컬럼명 강제 통일

            current_time = datetime.now().strftime("%Y%m%d_%H%M")
            filename = f"NewsList({news_date})daum.csv"
            
            output_dir = "Crawling"
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            save_path = os.path.join(output_dir, filename)
            
            df.to_csv(save_path, index=False, header=False, encoding="utf-8-sig")
            print(f"\n파일 저장 성공! -> {save_path} (총 {len(df)}건 완료)")
        else:
            print("수집된 데이터가 없습니다.")

    except Exception as e:
        print(f"크롤링 진행 중 치명적 에러 발생: {e}")

    finally:
        driver.quit()
        print("Chrome 브라우저를 안전하게 종료했습니다.")


if __name__ == "__main__":
    main()