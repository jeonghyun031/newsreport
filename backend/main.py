from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from saju import get_baseball_saju
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

# KBO 프로야구 구단 약칭 -> 검색어 매핑 (풀네임 및 띄어쓰기 무력화 보강)
SPORTS_ALIASES = {
    # 1. KIA
    "기아": "기아", "kia": "기아", "기아타이거즈": "기아", "kiatigers": "기아",
    # 2. 삼성
    "삼성": "삼성", "삼성라이온즈": "삼성", "samsunglions": "삼성",
    # 3. 두산
    "두산": "두산", "두산베어스": "두산", "doosanbears": "두산",
    # 4. LG
    "엘지": "lg", "lg": "lg", "엘지트윈스": "lg", "lgtwins": "lg",
    # 5. SSG
    "쓱": "ssg", "ssg": "ssg", "ssg랜더스": "ssg", "ssglanders": "ssg", "에스에스지": "ssg",
    # 6. 키움
    "키움": "키움", "키움히어로즈": "키움", "kiwoomheroes": "키움",
    # 7. 한화
    "한화": "한화", "한화이글스": "한화", "hanwhaeagles": "한화",
    # 8. 롯데
    "롯데": "롯데", "롯데자이언츠": "롯데", "lottegiants": "롯데",
    # 9. NC
    "엔씨": "nc", "nc": "nc", "엔씨다이노스": "nc", "ncdinos": "nc",
    # 10. KT
    "케이티": "kt", "kt": "kt", "케이티위즈": "kt", "kt위즈": "kt", "ktwiz": "kt"
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
    query: Optional[str] = None

@app.get("/api/news")
def get_news(query: Optional[str] = None):
    """
    AWS MySQL에서 KBO 뉴스 목록(메타 데이터)을 대단히 빠르게 조회합니다. (content 제외)
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # content 컬럼을 결과 SELECT에서 제외하여 데이터 전송 전송량을 99% 이상 감소시킵니다.
            sql = "SELECT date, title, press, category, url FROM news_articles WHERE category = 'KBO'"
            params = []
            
            if query and query.strip():
                # 검색어의 공백을 제거하고 소문자로 정규화 (띄어쓰기 유연성 확보)
                clean_query = query.strip().lower().replace(" ", "")
                # 스포츠 구단 약칭 치환
                resolved_query = SPORTS_ALIASES.get(clean_query, query.strip())
                
                # content 필드를 SELECT 하지는 않지만 WHERE 절 검색 조건으로는 활용할 수 있어, 본문 검색 기능은 보존됩니다.
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

@app.get("/api/news/content")
def get_news_content(title: str):
    """
    특정 기사의 본문 내용을 실시간으로 가져옵니다 (Lazy Loading 적용).
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            sql = "SELECT content FROM news_articles WHERE title = %s LIMIT 1"
            cursor.execute(sql, (title,))
            result = cursor.fetchone()
        conn.close()
        if result:
            return {"content": result["content"]}
        return {"content": "본문을 불러올 수 없습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"본문 조회 실패: {str(e)}")

@app.post("/api/summarize")
def summarize_news(payload: SummarizeRequest):
    """
    백엔드 내에서 검색어 기준 기사들을 직접 DB 스캔하고 Qwen3.5 LLM으로 3줄 요약합니다. (프론트/백엔드 본문 전송 비용 0원 최적화)
    """
    cfg = load_db_config()
    qwen_api_key = cfg.get("QWEN_API_KEY", "dcu_llm_wanhhrh13wu8eo38p98xkh06308p11hlmwdl48q8moixv1b6").strip()
    
    # 1. 백엔드 내부에서 쿼리에 매핑되는 기사 본문 직접 스캔 (최대 15개)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            sql = "SELECT content FROM news_articles WHERE category = 'KBO'"
            params = []
            
            if payload.query and payload.query.strip():
                clean_query = payload.query.strip().lower().replace(" ", "")
                resolved_query = SPORTS_ALIASES.get(clean_query, payload.query.strip())
                
                sql += " AND (title LIKE %s OR content LIKE %s)"
                search_pattern = f"%{resolved_query}%"
                params.extend([search_pattern, search_pattern])
                
            sql += " ORDER BY date DESC LIMIT 15"
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        conn.close()
        
        valid_articles = [row["content"].strip() for row in rows if row.get("content")]
    except Exception as db_err:
        raise HTTPException(status_code=500, detail=f"요약용 DB 기사 스캔 실패: {str(db_err)}")

    if not valid_articles:
        return {"summary": "요약할 뉴스 기사가 검색 조건에 존재하지 않습니다."}
        
    print(f"\n[LOG] /api/summarize: 백엔드 내부 스캔 완료. 요약 대상 기사 수 = {len(valid_articles)}개 (검색어: {payload.query or '전체'})")
        
    # 최대 15개 본문 병합
    summarize_target = "\n\n".join(valid_articles)
    
    # Qwen LLM 요약 프롬프트 조립
    prompt = f"""
    [No Thinking Mode / Fast Response]
    - Do NOT output any thinking, reasoning, chain of thought, or preamble. Output ONLY the final summary immediately.
    - Never hallucinate or add any details not present in the provided text. Rely strictly and only on the given facts.
    - Strict Constraint: Do NOT use any pre-trained external KBO general knowledge or other topics not present in the text below. Base your top 3 keywords and summaries ONLY and strictly on the text provided.
    
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
        "stream": False,
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

@app.get("/api/schedule")
def get_schedule():
    """
    KBO 경기 일정 데이터를 statistics_db.kbo_schedule 테이블로부터 조회하여 제공합니다.
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 기본 접속 DB가 articel_db이므로, 명시적으로 statistics_db.kbo_schedule을 쿼리합니다.
            sql = "SELECT date, time, away_team, home_team, stadium, status FROM statistics_db.kbo_schedule ORDER BY date ASC, time ASC"
            cursor.execute(sql)
            result = cursor.fetchall()
        conn.close()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"경기 일정 조회 실패: {str(e)}")

@app.get("/api/saju")
def get_pitcher_saju(pitcher: str):
    """
    Qwen LLM 기반 '야잘알 도사' 투수 사주풀이 결과를 생성하여 반환합니다.
    """
    if not pitcher or not pitcher.strip():
        raise HTTPException(status_code=400, detail="투수 이름을 입력해 주세요.")
    try:
        saju_text = get_baseball_saju(pitcher.strip())
        return {"saju": saju_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"사주 생성 실패: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
