# daum_piter_crawler.py
import datetime
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def get_tomorrow_game_links():
    options = webdriver.ChromeOptions()
    
    # 📌 Airflow(서버) 환경 필수 설정: 창 띄우지 않음
    options.add_argument('--headless=new')  
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    
    # 봇 감지 우회
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    tomorrow_links = []

    try:
        url = "https://sports.daum.net/schedule/kbo"
        driver.get(url)
        time.sleep(5)  # 리눅스 서버 환경에서는 로딩 시간을 넉넉히 주는 게 안전합니다.

        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        target_date = tomorrow.strftime("%m.%d")  

        rows = driver.find_elements(By.XPATH, "//table//tr")
        current_row_date = None

        for row in rows:
            date_elements = row.find_elements(By.CLASS_NAME, "td_date")
            if date_elements:
                span_element = date_elements[0].find_elements(By.CLASS_NAME, "num_date")
                if span_element:
                    current_row_date = span_element[0].text.strip()

            if current_row_date == target_date:
                link_elements = row.find_elements(By.CLASS_NAME, "link_game")
                if link_elements:
                    href = link_elements[0].get_attribute("href")
                    if href and href not in tomorrow_links:
                        tomorrow_links.append(href)
                        
        print(f"[{target_date}] 수집된 링크 개수: {len(tomorrow_links)}")
        return tomorrow_links

    finally:
        driver.quit()

if __name__ == "__main__":
    links = get_tomorrow_game_links()
    print(links)