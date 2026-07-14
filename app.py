import streamlit as st
import pandas as pd
import requests
import json
import time
import os
import pymysql

# 카테고리 매핑 사전 (KBO 전용으로 압축)
CATEGORY_MAP = {
    "KBO": "KBO",
}

# KBO 프로야구 구단 약칭 -> 검색 매핑 사전
SPORTS_ALIASES = {
    "기아": "kia", "kia": "kia", "삼성": "삼성", "두산": "두산", "엘지": "lg", "lg": "lg",
    "쓱": "ssg", "ssg": "ssg", "키움": "키움", "한화": "한화", "롯데": "롯데", "엔씨": "nc",
    "nc": "nc", "케이티": "kt", "kt": "kt"
}

# .env 로드하여 DB 접속 정보 및 기본 설정 가져오기
def load_db_config():
    env_dict = {}
    env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_dict[k.strip()] = v.strip()
    return env_dict

# DB에서 KBO 뉴스 기사 조회 함수
def get_news_from_db(category, query):
    cfg = load_db_config()
    aws_rds_endpoint = cfg.get("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com")
    db_port = cfg.get("DB_PORT", "3306")
    db_user = cfg.get("DB_USER", "admin")
    db_password = cfg.get("DB_PASSWORD", "12341234")
    db_name = cfg.get("DB_NAME", "test_db")

    try:
        conn = pymysql.connect(
            host=aws_rds_endpoint,
            port=int(db_port),
            user=db_user,
            password=db_password,
            database=db_name,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn.cursor() as cursor:
            # category가 KBO인 기사만 필터링하여 조회 (KBO 특화)
            sql = "SELECT date, title, press, content, category FROM news_articles WHERE category = 'KBO'"
            params = []

            # 검색어 필터
            if query and query.strip():
                sql += " AND (title LIKE %s OR content LIKE %s)"
                search_pattern = f"%{query.strip()}%"
                params.extend([search_pattern, search_pattern])

            sql += " ORDER BY date DESC"
            cursor.execute(sql, params)
            result = cursor.fetchall()
            conn.close()
            return result
    except Exception as e:
        st.error(f"⚠️ DB 연결 실패: {e}")
        return []

# Qwen LLM 요약 스트리밍 로직
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
        try:
            chunk = json.loads(data_str)
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                yield content
        except Exception:
            continue

# 메인 웹 대시보드 인터페이스
def main():
    st.set_page_config(page_title="KBO daily news alarm ⚾", layout="wide")
    
    # daily news alarm 전용 CSS 주입 (야구 테마의 깔끔한 다크/라이트 그레이톤)
    st.markdown("""
        <style>
            .stApp {
                background-color: #f5f6f8;
            }
            .title-header {
                text-align: center;
                font-family: 'Inter', sans-serif;
                font-size: 3.2rem;
                font-weight: 800;
                margin-top: 1rem;
                margin-bottom: 2rem;
                color: #0f2a4a; /* 야구 구단 시안 느낌의 세련된 딥 네이비 */
            }
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
    
    st.markdown('<div class="title-header">KBO daily news alarm ⚾</div>', unsafe_allow_html=True)

    # API 인증 설정 사이드바 구성
    with st.sidebar:
        st.header("🔑 API 인증 설정")
        
        with st.form("api_keys_form"):
            qwen_api_key_input = st.text_input(
                "Qwen API Key",
                value=st.session_state.get("QWEN_API_KEY", "dcu_llm_wanhhrh13wu8eo38p98xkh06308p11hlmwdl48q8moixv1b6"),
                type="password",
                placeholder="Qwen API 키 입력"
            )
            submit_btn = st.form_submit_button("💾 설정 저장")
            if submit_btn:
                st.session_state["QWEN_API_KEY"] = qwen_api_key_input
                st.success("인증 설정이 저장되었습니다!")
                st.rerun()
        
        st.markdown("---")
        # DB 상태 요약 정보 표시
        cfg = load_db_config()
        st.subheader("📊 DB 연결 상태")
        st.text(f"Host: {cfg.get('AWS_RDS_ENDPOINT', 'N/A')[:20]}...")
        st.text(f"DB Name: {cfg.get('DB_NAME', 'N/A')}")
        st.text(f"User: {cfg.get('DB_USER', 'N/A')}")

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
                placeholder="🔍 KBO 구단명 또는 키워드를 검색하고 Enter를 누르세요.",
                label_visibility="collapsed"
            )

        # DB 뉴스 로드
        query_clean = search_query.strip().lower()
        if query_clean in SPORTS_ALIASES:
            resolved_query = SPORTS_ALIASES[query_clean]
        else:
            resolved_query = search_query.strip()

        # 기사 목록 조회
        news_list = get_news_from_db(selected_category_val, resolved_query)

        if search_query.strip():
            st.markdown(f"#### 🔍 KBO DB 검색 결과 (총 {len(news_list)}건)")
        else:
            st.markdown(f"#### 📰 최신 KBO DB 뉴스 목록 (총 {len(news_list)}건)")

        if news_list:
            # 기사들을 데이터프레임으로 변환하여 표 형태로 리스트업
            news_df = pd.DataFrame(news_list)
            
            # 본문을 제외한 메타 정보만 뷰어로 보기 좋게 구성
            display_df = news_df[["date", "category", "press", "title"]]
            st.dataframe(display_df, use_container_width=True, height=250)

            # 기사 요약 처리
            valid_articles = [row['content'].strip() for row in news_list if row['content'].strip()]
            
            if valid_articles:
                summarize_target = "\n\n".join(valid_articles[:15]) # 속도와 토큰 제한을 위해 최대 15개 본문 병합
                
                # Qwen LLM 요약 프롬프트 조립 (KBO 특화, 할루시네이션 방지 및 /no_think 적용)
                prompt = f"""
                [No Thinking Mode / Fast Response]
                - Do NOT output any thinking, reasoning, chain of thought, or preamble. Output ONLY the final summary immediately.
                - Never hallucinate or add any details not present in the provided text. Rely strictly and only on the given facts.
                - Summarize the provided KBO baseball news text in exactly 3 bullet points in Korean.
                - Focus on key concepts and facts from the news.
                
                Text to summarize:
                {summarize_target}
                """
                
                st.markdown("<h3 style='margin-top: 1.5rem;'>📋 Qwen 3.5 AI 3줄 요약 결과</h3>", unsafe_allow_html=True)
                summary_placeholder = st.empty()
                summary_text = ""
                
                try:
                    with st.spinner("Qwen AI가 KBO DB 기사를 신속하게 분석 요약하는 중..."):
                        for text_chunk in ask_qwen_stream(prompt, qwen_api_key):
                            summary_text += text_chunk
                            summary_placeholder.markdown(summary_text)
                except Exception as e:
                    st.error(f"요약 중 오류가 발생했습니다: {e}")
        else:
            st.info("검색 조건에 맞는 KBO DB 뉴스 기사가 없습니다.")

    with col2:
        st.markdown("<h4 style='font-weight: bold; margin-bottom: 1rem; color: #000000;'>많이 본 뉴스</h4>", unsafe_allow_html=True)
        
        # 더미 위젯 렌더링
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
