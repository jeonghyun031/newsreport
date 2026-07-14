from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import pymysql
import requests
import json
from typing import List, Optional

app = FastAPI(title="KBO News Briefing API")

# React 프론트엔드 연동을 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 로컬 개발 환경용 전체 허용 (배포 시 제한 필요)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# KBO 프로야구 구단 약칭 -> 검색어 매핑
SPORTS_ALIASES = {
    "기아": "kia", "kia": "kia", "삼성": "삼성", "두산": "두산", "엘지": "lg", "lg": "lg",
    "쓱": "ssg", "ssg": "ssg", "키움": "키움", "한화": "한화", "롯데": "롯데", "엔씨": "nc",
    "nc": "nc", "케이티": "kt", "kt": "kt"
}

# .env 환경 설정 로드
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

# DB 연결 헬퍼 함수
def get_db_connection():
    cfg = load_db_config()
    aws_rds_endpoint = cfg.get("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com")
    db_port = cfg.get("DB_PORT", "3306")
    db_user = cfg.get("DB_USER", "admin")
    db_password = cfg.get("DB_PASSWORD", "12341234")
    db_name = cfg.get("DB_NAME", "test_db")
    
    return pymysql.connect(
        host=aws_rds_endpoint,
        port=int(db_port),
        user=db_user,
        password=db_password,
        database=db_name,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

class SummarizeRequest(BaseModel):
    articles: List[str]

@app.get("/api/news")
def get_news(query: Optional[str] = None):
    """
    AWS MySQL에서 KBO 뉴스 목록을 조회합니다.
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 기본적으로 category가 'KBO'인 기사만 필터링 조회 (news_articles 테이블 연동 및 url 추가)
            sql = "SELECT date, title, press, content, category, url FROM news_articles WHERE category = 'KBO'"
            params = []
            
            if query and query.strip():
                clean_query = query.strip().lower()
                # 스포츠 구단 약칭 치환
                resolved_query = SPORTS_ALIASES.get(clean_query, query.strip())
                
                sql += " AND (title LIKE %s OR content LIKE %s)"
                search_pattern = f"%{resolved_query}%"
                params.extend([search_pattern, search_pattern])
                
            sql += " ORDER BY date DESC"
            cursor.execute(sql, params)
            result = cursor.fetchall()
        conn.close()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 조회 실패: {str(e)}")

@app.post("/api/summarize")
def summarize_news(payload: SummarizeRequest):
    """
    Qwen3.5 LLM을 활용해 할루시네이션 방지 및 생각을 생략한 빠른 3줄 요약을 제공합니다.
    """
    cfg = load_db_config()
    qwen_api_key = cfg.get("QWEN_API_KEY", "dcu_llm_wanhhrh13wu8eo38p98xkh06308p11hlmwdl48q8moixv1b6").strip()
    
    valid_articles = [art.strip() for art in payload.articles if art.strip()]
    if not valid_articles:
        raise HTTPException(status_code=400, detail="요약할 기사 본문이 없습니다.")
        
    # 터미널 모니터링용 로그 출력
    print(f"\n[LOG] /api/summarize: 수신된 KBO 필터링 기사 수 = {len(valid_articles)}개")
        
    # 최대 15개 본문 병합 (토큰 및 시간 제한)
    summarize_target = "\n\n".join(valid_articles[:15])
    
    # Qwen LLM 요약 프롬프트 조립 (필터링된 컨텍스트 제한 강화)
    prompt = f"""
    [No Thinking Mode / Fast Response]
    - Do NOT output any thinking, reasoning, chain of thought, or preamble. Output ONLY the final summary immediately.
    - Never hallucinate or add any details not present in the provided text. Rely strictly and only on the given facts.
    - Strict Constraint: Do NOT use any pre-trained external KBO general knowledge or other topics not present in the text below. Base your top 3 keywords and summaries ONLY and strictly on the text provided. (e.g., If the input only contains articles about 'Hanwha', do not summarize or extract keywords about other teams like 'KIA' or 'Samsung').
    
    Task:
    Analyze the provided KBO baseball news articles and identify the top 3 most frequently mentioned or highly important keywords/topics within this specific text.
    For each of the 3 keywords, generate exactly 1 summary sentence strictly based on the facts in the text.
    
    Output Format (Exactly 3 lines of bullet points in Korean):
    - **[Keyword 1]** Summary sentence about Keyword 1.
    - **[Keyword 2]** Summary sentence about Keyword 2.
    - **[Keyword 3]** Summary sentence about Keyword 3.
    
    Text to analyze:
    {summarize_target}
    """
    
    url = "https://code.cu.ac.kr/llm/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {qwen_api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    data = {
        "model": "Qwen/Qwen3.5-35B-A3B-FP8",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,  # API 속도를 높이기 위해 단발성 응답 처리
    }
    
    try:
        resp = requests.post(url, headers=headers, json=data)
        resp.raise_for_status()
        resp_data = resp.json()
        
        choices = resp_data.get("choices", [])
        if not choices:
            raise HTTPException(status_code=502, detail="LLM 응답 데이터가 올바르지 않습니다.")
            
        summary_content = choices[0].get("message", {}).get("content", "").strip()
        return {"summary": summary_content}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM API 연동 실패: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
