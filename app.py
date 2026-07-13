import streamlit as st
import pandas as pd
import requests
import json
import time
import random
import os
from bs4 import BeautifulSoup

# 차단 방지를 위한 다양한 브라우저 User-Agent 목록
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0'
]

CSV_FILE = "naver_news_results.csv"

# 카테고리 매핑 사전
CATEGORY_MAP = {
    "K-League": "K리그 국내축구",
    "KBO": "KBO 국내야구",
    "MLB": "MLB 해외야구",
    "EPL": "EPL 해외축구",
    "ALL": ""
}

# 스포츠 구단 약칭 -> 정식 명칭 매핑 사전
SPORTS_ALIASES = {
    # K리그 축구 구단
    "포항": "포항 스틸러스",
    "울산": "울산 HD FC",
    "전북": "전북 현대 모터스",
    "서울": "FC서울",
    "수원": "수원 삼성 블루윙즈",
    
    # KBO 야구 구단
    "기아": "KIA 타이거즈",
    "kia": "KIA 타이거즈",
    "삼성": "삼성 라이온즈",
    "두산": "두산 베어스",
    "엘지": "LG 트윈스",
    "lg": "LG 트윈스",
    "쓱": "SSG 랜더스",
    "ssg": "SSG 랜더스",
    "키움": "키움 히어로즈",
    "한화": "한화 이글스",
    "롯데": "롯데 자이언츠",
    "엔씨": "NC 다이노스",
    "nc": "NC 다이노스",
    "케이티": "KT 위즈",
    "kt": "KT 위즈",
    
    # EPL 해외축구 구단
    "토트넘": "토트넘 홋스퍼 FC",
    "맨유": "맨체스터 유나이티드 FC",
    "맨시티": "맨체스터 시티 FC",
    "아스날": "아스널 FC",
    "리버풀": "리버풀 FC",
    "첼시": "첼시 FC",
    
    # MLB 해외야구 구단
    "다저스": "LA 다저스",
    "양키스": "뉴욕 양키스",
    "샌디에이고": "샌디에이고 파드리스",
    "샌프란시스코": "샌프란시스코 자이언츠",
    "에인절스": "LA 에인절스",
    "토론토": "토론토 블루제이스"
}


### [기능 1] 백그라운드 뉴스 크롤러 로직 ###
def auto_crawl_news(client_id, client_secret, search_query, status_obj):
    naver_news_df = pd.DataFrame(columns=('press', 'title', 'time', 'article'))
    idx = 0

    api_headers = {
        'X-Naver-Client-Id': client_id,
        'X-Naver-Client-Secret': client_secret
    }
    
    # 검색 데이터 양을 늘려(display=30) 네이버 인링크 매칭 성공률을 높입니다.
    url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(search_query)}&display=30&start=1&sort=sim"
    
    try:
        status_obj.write(f"📡 네이버 Open API 호출 중... (검색어: {search_query})")
        response = requests.get(url, headers=api_headers)
        if response.status_code != 200:
            err_msg = "API 오류"
            try:
                err_data = response.json()
                err_msg = err_data.get('errorMessage', err_msg)
            except:
                pass
            status_obj.write(f"❌ Naver API 호출 실패 (상태 코드: {response.status_code}, 메시지: {err_msg})")
            return pd.DataFrame()
            
        data = response.json()
        items = data.get('items', [])
        
        status_obj.write(f"📰 검색 결과 {len(items)}개의 뉴스 기사 분석 시작...")
        
        for i, item in enumerate(items):
            link = item.get('link')
            # 모바일 및 PC 도메인 정합 필터링
            is_naver_news = link and ("news.naver.com" in link or "sports.naver.com" in link or "sports.news" in link or "sports.news.naver.com" in link)
            
            if is_naver_news:
                status_obj.write(f"🔗 [{i+1}/{len(items)}] 네이버 뉴스 본문 수집 중: {item.get('title', '')[:25]}...")
                
                headers = {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Referer': 'https://search.naver.com/search.naver?where=news'
                }
                time.sleep(random.uniform(0.5, 1.2))  # 봇 차단 방지 딜레이
                
                try:
                    n_link = requests.get(link, headers=headers)
                    l_soup = BeautifulSoup(n_link.text, 'html.parser')
                    
                    # 1. 언론사 추출
                    press = "알 수 없음"
                    press_img = l_soup.find('img')
                    if press_img:
                        if press_img.has_attr('title'):
                            press = press_img['title']
                        elif press_img.has_attr('alt'):
                            press = press_img['alt']
                    
                    # 2. 제목 추출
                    title_el = l_soup.find('h2', {'class': 'media_end_head_headline'})
                    if not title_el:
                        title_el = l_soup.find('h4', {'class': 'title'})
                    if not title_el:
                        title_el = l_soup.find('h2', {'class': 'title'})
                        
                    title = title_el.get_text().strip() if title_el else item.get('title', '제목 없음')
                    title = BeautifulSoup(title, 'html.parser').get_text()
                    
                    # 3. 작성시간 추출
                    time_el = l_soup.find('span', {'class': 'media_end_head_info_datestamp_time'})
                    if not time_el:
                        time_el = l_soup.find('span', {'class': 'date'})
                    if not time_el:
                        time_div = l_soup.find('div', {'class': 'info'})
                        if time_div and time_div.find('span'):
                            time_el = time_div.find('span')
                            
                    news_time = time_el.get_text().strip() if time_el else "시간 정보 없음"
                    
                    # 4. 본문 내용 추출 (다양한 네이버 뉴스/스포츠 뉴스 레이아웃 대응)
                    selectors = [
                        '#dic_area',
                        '#newsEndContents',
                        '#news_end',
                        '.news_end_contents',
                        '#articleBodyContents',
                        '.go_txt',
                        '#articeBody'
                    ]
                    article_el = None
                    for selector in selectors:
                        article_el = l_soup.select_one(selector)
                        if article_el:
                            break
                        
                    article = article_el.get_text().strip() if article_el else "본문 없음"
                    
                    # 기사 저장
                    naver_news_df.loc[idx] = [press, title, news_time, article]
                    idx += 1
                except Exception as e:
                    status_obj.write(f"⚠️ 기사 본문 파싱 스킵 (에러: {e})")
                    continue
    except Exception as e:
        status_obj.write(f"❌ 네트워크 연결 중 오류 발생: {e}")
        return pd.DataFrame()
        
    return naver_news_df


### [기능 2] Qwen LLM 요약 스트리밍 로직 ###
def ask_qwen_stream(prompt, apikey):
    apikey = apikey.strip()
    url = "https://code.cu.ac.kr/llm/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {apikey}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    data = {
        "model": "Qwen/Qwen3.5-35B-A3B-FP8",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    
    resp = requests.post(url, headers=headers, json=data, stream=True)
    resp.raise_for_status()

    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data:"):
            continue
        data_str = line[len("data:"):].strip()
        if data_str == "[DONE]":
            break
        chunk = json.loads(data_str)
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        
        reasoning = delta.get("reasoning", "")
        content = delta.get("content", "")
        
        if reasoning:
            yield "reasoning", reasoning
        if content:
            yield "content", content


### [기능 3] 메인 웹 대시보드 인터페이스 ###
def main():
    # 시안 레이아웃에 맞춰 wide로 설정
    st.set_page_config(page_title="daily news alarm", layout="wide")
    
    # daily news alarm 전용 CSS 주입 (회색 배경 톤 및 UI 디테일 구현)
    st.markdown("""
        <style>
            .stApp {
                background-color: #e6e6e6;
            }
            div[data-testid="stForm"] {
                border: none;
                padding: 0;
            }
            .title-header {
                text-align: center;
                font-family: 'Inter', sans-serif;
                font-size: 3.5rem;
                font-weight: 700;
                margin-top: 1rem;
                margin-bottom: 2rem;
                color: #000000;
            }
            .sidebar-title {
                color: #000000;
                font-weight: bold;
            }
            /* 많이 본 뉴스 카드 스타일 */
            .news-card {
                background-color: #ffffff;
                padding: 14px 18px;
                border-radius: 4px;
                margin-bottom: 12px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.06);
                border: 1px solid #e0e0e0;
                color: #000000;
                font-family: 'Inter', sans-serif;
                line-height: 1.4;
            }
            .news-card-title {
                font-weight: 500;
                font-size: 0.95rem;
            }
        </style>
    """, unsafe_allow_html=True)
    
    # 1. 메인 타이틀 렌더링
    st.markdown('<div class="title-header">daily news alarm</div>', unsafe_allow_html=True)

    # API 인증 설정 사이드바 구성
    with st.sidebar:
        st.header("🔑 API 인증 설정")
        
        with st.form("api_keys_form"):
            st.subheader("Naver Open API")
            naver_client_id_input = st.text_input(
                "Client ID", 
                value=st.session_state.get("NAVER_CLIENT_ID", "S8onUIHCUrmUeYyLrzIk"),
                placeholder="인증 ID를 입력하세요"
            )
            naver_client_secret_input = st.text_input(
                "Client Secret", 
                value=st.session_state.get("NAVER_CLIENT_SECRET", ""),
                type="password", 
                placeholder="비밀 키를 입력하세요"
            )
            
            st.subheader("Qwen LLM API")
            qwen_api_key_input = st.text_input(
                "Qwen API Key",
                value=st.session_state.get("QWEN_API_KEY", "dcu_llm_wanhhrh13wu8eo38p98xkh06308p11hlmwdl48q8moixv1b6"),
                type="password",
                placeholder="Qwen API 키 입력"
            )
            
            submit_btn = st.form_submit_button("💾 설정 저장")
            if submit_btn:
                st.session_state["NAVER_CLIENT_ID"] = naver_client_id_input
                st.session_state["NAVER_CLIENT_SECRET"] = naver_client_secret_input
                st.session_state["QWEN_API_KEY"] = qwen_api_key_input
                st.success("인증 설정이 저장되었습니다!")
                st.rerun()
        
        st.markdown("---")
        st.info("💡 Naver API 인증 키가 없으시면 뉴스 수집(크롤링)이 제한됩니다. 요약 기능은 Qwen API 키만으로도 수작업 입력을 통해 사용 가능합니다.")

    # 세션에서 인증 키값 로드
    naver_client_id = st.session_state.get("NAVER_CLIENT_ID", "S8onUIHCUrmUeYyLrzIk")
    naver_client_secret = st.session_state.get("NAVER_CLIENT_SECRET", "")
    qwen_api_key = st.session_state.get("QWEN_API_KEY", "dcu_llm_wanhhrh13wu8eo38p98xkh06308p11hlmwdl48q8moixv1b6")

    # 3단 컬럼이 아닌 시안대로 왼쪽(메인 기능 3/4)과 오른쪽(많이 본 뉴스 1/4) 배치
    col1, col2 = st.columns([3, 1])

    with col1:
        # 입력 폼 구성 (카테고리 선택 + 검색 인풋이 가로로 한 행에 배치)
        input_col1, input_col2 = st.columns([1.5, 3.5])
        
        with input_col1:
            selected_cat_key = st.selectbox(
                label="Category",
                options=list(CATEGORY_MAP.keys()),
                label_visibility="collapsed"
            )
            selected_category_val = CATEGORY_MAP[selected_cat_key]
            
        with input_col2:
            search_query = st.text_input(
                label="Search Input",
                placeholder="🔍 검색어를 입력하고 Enter를 누르세요.",
                label_visibility="collapsed"
            )

        # 검색어 입력 시 크롤링 및 요약 자동 시작
        if search_query.strip():
            # 스포츠 약칭 예외 처리 (정식 명칭으로 치환)
            query_clean = search_query.strip().lower()
            if query_clean in SPORTS_ALIASES:
                resolved_query = SPORTS_ALIASES[query_clean]
            else:
                resolved_query = search_query.strip()
                
            # 검색어 및 카테고리 조합
            if selected_category_val:
                search_term = f"{selected_category_val} {resolved_query}".strip()
            else:
                search_term = resolved_query
                
            # API 키 사전 검증
            if not naver_client_secret:
                st.error("❌ Naver Client Secret 키가 설정되어 있지 않습니다. 사이드바에 설정 값을 입력하고 저장해 주세요.")
                return
                
            # 백그라운드 크롤링 수행
            crawl_status = st.status(f"🔍 '{search_term}' 관련 최신 뉴스 백그라운드 수집 중...", expanded=True)
            df = auto_crawl_news(naver_client_id, naver_client_secret, search_term, crawl_status)
            
            if df.empty or 'article' not in df.columns:
                crawl_status.update(label="❌ 기사 수집 실패", state="error", expanded=False)
                st.error("기사를 수집해오지 못했거나 본문을 분석할 수 있는 네이버 뉴스가 없습니다. API 키 및 네트워크를 확인해 주세요.")
                return
            
            # 수집된 기사의 본문들 추출
            valid_articles = [art.strip() for art in df['article'].dropna().tolist() if art.strip() and art.strip() != "본문 없음"]
            
            if not valid_articles:
                crawl_status.update(label="⚠️ 수집 본문 없음", state="error", expanded=False)
                st.error("검색된 뉴스는 존재하나 상세 본문 내용을 크롤링하지 못했습니다.")
                return
                
            # CSV 임시 백업 저장
            df.to_csv(CSV_FILE, index=False, encoding='utf-8-sig')
            
            # 최종 요약에 전달할 기사 텍스트 병합
            summarize_target = "\n\n[다음 기사 본문]\n" + "\n\n".join(valid_articles)
            crawl_status.update(label=f"✅ 총 {len(valid_articles)}개의 뉴스 수집 완료!", state="complete", expanded=False)
            
            # Qwen LLM 요약 프롬프트 조립
            prompt = f"""
            **Instructions** :
            - You are an expert assistant that summarizes text into **Korean language**.
            - Your task is to summarize the **text** sentences in **Korean language**.
            - Your summaries should include the following :
                - Omit duplicate content, but increase the summary weight of duplicate content. #중복금지 3줄
                - Summarize by emphasizing concepts and arguments rather than case evidence.
                - Summarize in 3 lines.
                - Use the format of a bullet point.
            -text : {summarize_target}
            """
            
            st.markdown("<h3 style='margin-top: 1.5rem;'>요약 결과</h3>", unsafe_allow_html=True)
            summary_placeholder = st.empty()
            summary_text = ""
            
            try:
                with st.spinner("Qwen AI가 기사를 분석하여 요약하는 중..."):
                    for chunk_type, text_chunk in ask_qwen_stream(prompt, qwen_api_key):
                        if chunk_type == "reasoning":
                            continue
                        elif chunk_type == "content":
                            summary_text += text_chunk
                            summary_placeholder.markdown(summary_text)
            except Exception as e:
                st.error(f"요약 중 오류가 발생했습니다: {e}")

    with col2:
        # 많이 본 뉴스 헤더 표시
        st.markdown("<h4 style='font-weight: bold; margin-bottom: 1rem; color: #000000;'>많이 본 뉴스</h4>", unsafe_allow_html=True)
        
        # 시안에 있는 1~5번 뉴스 더미 위젯 렌더링
        dummy_news = [
            "1. [단독] 마르티네스 전 감독, 한국 사령탑 관심 표명",
            "2. '정몽규 사임' 후폭풍... 박지성 중심 혁신위 첫발",
            "3. '완델손 원맨쇼' 포항, K리그 선두 추격 불붙었다",
            "4. 경찰, 홍명보 전 감독 선임 의혹 수사 속도 낸다",
            "5. KFA, K3·K4리그 챔피언십 및 승강제 개편 발표"
        ]
        
        for news in dummy_news:
            st.markdown(f"""
                <div class="news-card">
                    <span class="news-card-title">{news}</span>
                </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
