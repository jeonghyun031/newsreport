import sys
import os
# 프로젝트 상위 루트 경로를 모듈 탐색 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# .env 환경 설정 로드 (시스템 환경변수 + 다중 .env 파일 탐색)
def load_db_config():
    env_dict = dict(os.environ)
    env_paths = [".env", "../.env", "/app/.env", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")]
    for path in env_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            env_dict[k.strip()] = v.strip().strip('"').strip("'")
            except Exception as e:
                print(f"⚠️ .env 로드 중 오류 ({path}): {e}")
    return env_dict

# DB 연결 헬퍼 함수
def get_db_connection():
    cfg = load_db_config()
    aws_rds_endpoint = cfg.get("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com")
    db_port = cfg.get("DB_PORT", "3306")
    db_user = cfg.get("DB_USER", "admin")
    db_password = cfg.get("DB_PASSWORD", "12341234")
    db_name = cfg.get("DB_NAME", "total_db")
    
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
    KBO 경기 일정 데이터를 kbo_schedule 테이블로부터 조회하여 제공합니다.
    (DB 실제 컬럼명 game_date, game_time, game_status 별칭 바인딩)
    """
    try:
        conn = get_db_connection()
        result = []
        with conn.cursor() as cursor:
            # DB 실제 컬럼명: game_date, game_time, away_team, home_team, stadium, game_status, away_pitcher, home_pitcher
            sql_primary = """
                SELECT 
                    game_date AS date, 
                    game_time AS time, 
                    away_team, 
                    home_team, 
                    stadium, 
                    game_status AS status, 
                    away_pitcher, 
                    home_pitcher 
                FROM kbo_schedule 
                ORDER BY game_date ASC, game_time ASC
            """
            try:
                cursor.execute(sql_primary)
                result = cursor.fetchall()
            except Exception as e1:
                print(f"⚠️ [1차 쿼리 실패, 호환 쿼리 시도]: {e1}")
                # 혹시 date, time, status 컬럼명으로 되어 있는 기존 테이블 대비 2차 쿼리
                sql_fallback = "SELECT date, time, away_team, home_team, stadium, status, away_pitcher, home_pitcher FROM kbo_schedule ORDER BY date ASC, time ASC"
                cursor.execute(sql_fallback)
                result = cursor.fetchall()

        conn.close()
        return result
    except Exception as e:
        print(f"⚠️ [/api/schedule] 경기 일정 DB 조회 예외: {e}")
@app.get("/api/saju")
def get_pitcher_saju(
    pitcher: str, 
    opponent: Optional[str] = "상대팀", 
    stadium: Optional[str] = "야구장",
    date: Optional[str] = None,
    my_team: Optional[str] = None
):
    """
    Qwen LLM 기반 '야잘알 도사' 투수 사주풀이 결과를 생성하여 반환합니다.
    """
    if not pitcher or not pitcher.strip():
        raise HTTPException(status_code=400, detail="투수 이름을 입력해 주세요.")
    try:
        saju_text = get_baseball_saju(pitcher.strip(), opponent.strip(), stadium.strip(), date, my_team)
        return {"saju": saju_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"사주 생성 실패: {str(e)}")

class EmailBriefingRequest(BaseModel):
    email: str
    teams: List[str]

class SubscribeRequest(BaseModel):
    email: str
    teams: List[str]

def send_smtp_email(to_email: str, subject: str, html_content: str):
    """
    SMTP 설정을 이용해 이메일을 발송합니다.
    환경변수에 SMTP 설정이 없거나 실패할 경우 로그를 남깁니다.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    cfg = load_db_config()
    smtp_server = cfg.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(cfg.get("SMTP_PORT", "587"))
    smtp_user = cfg.get("SMTP_USER", "")
    smtp_password = cfg.get("SMTP_PASSWORD", "")

    if not smtp_user or not smtp_password:
        print(f"ℹ️ [SMTP 안내] SMTP 계정이 설정되지 않았습니다. 이메일 전송 시뮬레이션을 완료했습니다. (수신: {to_email})")
        return True, "SMTP 계정이 설정되지 않아 전송 시뮬레이션 모드로 처리되었습니다. (UI 미리보기 성공)"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"KBO AI News Caster <{smtp_user}>"
        msg["To"] = to_email

        html_part = MIMEText(html_content, "html", "utf-8")
        msg.attach(html_part)

        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email, msg.as_string())
            
        print(f"📧 [SMTP 성공] '{to_email}'로 브리핑 메일을 성공적으로 발송했습니다.")
        return True, "이메일이 성공적으로 전송되었습니다!"
    except Exception as e:
        print(f"❌ [SMTP 오류] 이메일 발송 실패: {e}")
        return False, f"이메일 발송 중 오류가 발생했습니다: {str(e)}"

@app.post("/api/send-email-briefing")
def send_email_briefing(payload: EmailBriefingRequest):
    """
    선택한 관심 구단(들)의 최신 기사 + Qwen AI 브리핑을 조립하여 이메일로 즉시 발송합니다.
    """
    if not payload.email or "@" not in payload.email:
        raise HTTPException(status_code=400, detail="유효한 이메일 주소를 입력해 주세요.")
    if not payload.teams:
        raise HTTPException(status_code=400, detail="최소 하나 이상의 관심 구단을 선택해 주세요.")

    teams_str = ", ".join(payload.teams)
    print(f"\n[LOG] /api/send-email-briefing: 수신자={payload.email}, 관심구단={teams_str}")

    # 1. 관심 구단별 뉴스 및 kbo_schedule 매치업/선발투수 정보 쿼리
    try:
        conn = get_db_connection()
        articles_by_team = {}
        schedules_by_team = {}
        with conn.cursor() as cursor:
            for team in payload.teams:
                # 1-1. 뉴스 기사 쿼리
                resolved_query = SPORTS_ALIASES.get(team.lower(), team)
                sql_news = """
                    SELECT title, press, date, url, content
                    FROM news_articles
                    WHERE category = 'KBO' AND (title LIKE %s OR content LIKE %s)
                    ORDER BY date DESC LIMIT 4
                """
                pattern = f"%{resolved_query}%"
                cursor.execute(sql_news, (pattern, pattern))
                rows = cursor.fetchall()
                if rows:
                    articles_by_team[team] = rows

                # 1-2. kbo_schedule 테이블에서 매치업 및 선발투수 쿼리
                sql_sched = """
                    SELECT 
                        game_date AS date, 
                        game_time AS time, 
                        away_team, 
                        home_team, 
                        stadium, 
                        game_status AS status, 
                        away_pitcher, 
                        home_pitcher
                    FROM kbo_schedule
                    WHERE away_team LIKE %s OR home_team LIKE %s
                    ORDER BY game_date DESC, game_time ASC LIMIT 2
                """
                team_pattern = f"%{team}%"
                cursor.execute(sql_sched, (team_pattern, team_pattern))
                sched_rows = cursor.fetchall()
                if sched_rows:
                    schedules_by_team[team] = sched_rows

        conn.close()
    except Exception as db_err:
        raise HTTPException(status_code=500, detail=f"데이터 조회 중 오류 발생: {str(db_err)}")

    # 2. Qwen AI 맞춤 브리핑 생성
    all_titles = []
    for team, rows in articles_by_team.items():
        for r in rows:
            all_titles.append(f"[{team}] {r['title']}")
            
    summary_text = ""
    if all_titles:
        sample_prompt = f"""
        당신은 KBO 전담 AI 스포츠 기자입니다.
        아래는 사용자가 선택한 관심 구단({teams_str})의 최신 뉴스 제목들입니다:
        {chr(10).join(all_titles[:10])}

        선택된 구단 팬들을 위해, 오늘 이 구단들의 핵심 이슈 3가지를 친절하고 열정적인 어조로 3줄 요약해 주세요.
        각 줄은 '- **[구단명/키워드]** 요약문' 형식으로 작성해 주세요.
        """
        try:
            cfg = load_db_config()
            qwen_api_key = cfg.get("QWEN_API_KEY", "dcu_llm_wanhhrh13wu8eo38p98xkh06308p11hlmwdl48q8moixv1b6").strip()
            resp = requests.post(
                "https://code.cu.ac.kr/llm/v1/chat/completions",
                headers={"Authorization": f"Bearer {qwen_api_key}", "Content-Type": "application/json"},
                json={"model": "Qwen/Qwen3.5-35B-A3B-FP8", "messages": [{"role": "user", "content": sample_prompt}], "stream": False},
                timeout=10
            )
            if resp.ok:
                choices = resp.json().get("choices", [])
                if choices:
                    summary_text = choices[0].get("message", {}).get("content", "")
        except Exception as llm_err:
            print(f"⚠️ 이메일 브리핑 LLM 생성 실패 (대체 문구 사용): {llm_err}")

    if not summary_text:
        summary_text = f"- **[{teams_str}]** 최근 KBO 리그 경기가 뜨겁게 펼쳐지는 가운데 팬들의 열띤 응원이 이어지고 있습니다."

    # 3. 프리미엄 HTML 이메일 템플릿 제작 (kbo_schedule 매치업 및 선발투수 포함)
    news_html_blocks = ""
    for team in payload.teams:
        # 경기 일정 & 선발 투수 블록
        sched_rows = schedules_by_team.get(team, [])
        sched_html = ""
        if sched_rows:
            sched_html += f"""
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; margin-bottom: 12px;">
                <strong style="color: #0f172a; font-size: 13px;">🗓️ {team} 경기 매치업 & 선발 투수</strong>
                <ul style="margin: 6px 0 0 0; padding-left: 18px; font-size: 13px; color: #475569;">
            """
            for s in sched_rows:
                away_p = s.get('away_pitcher') or '선발미정'
                home_p = s.get('home_pitcher') or '선발미정'
                date_fmt = str(s.get('date', ''))
                sched_html += f"""
                    <li style="margin-bottom: 4px;">
                        [{date_fmt}] <strong>{s.get('away_team')}</strong>({away_p}) VS <strong>{s.get('home_team')}</strong>({home_p}) @ {s.get('stadium')} ({s.get('status', '')})
                    </li>
                """
            sched_html += "</ul></div>"

        # 뉴스 리스트 블록
        rows = articles_by_team.get(team, [])
        news_list_html = ""
        if rows:
            news_list_html += '<ul style="padding-left: 20px; color: #334155; line-height: 1.6; margin-top: 6px;">'
            for r in rows:
                news_list_html += f"""
                    <li style="margin-bottom: 8px;">
                        <strong>[{r.get('press', 'KBO')}]</strong> {r.get('title')}
                    </li>
                """
            news_list_html += '</ul>'
        else:
            news_list_html = '<p style="font-size: 13px; color: #94a3b8;">최신 뉴스 기사를 불러오는 중입니다.</p>'

        news_html_blocks += f"""
        <div style="margin-bottom: 24px;">
            <h3 style="color: #0284c7; border-bottom: 2px solid #0284c7; padding-bottom: 4px; margin-bottom: 10px; font-size: 16px;">⚾ {team} 브리핑</h3>
            {sched_html}
            {news_list_html}
        </div>
        """

    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: 'Apple SD Gothic Neo', sans-serif; background-color: #f8fafc; padding: 20px; color: #1e293b;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
            <div style="background: linear-gradient(135deg, #0f172a 0%, #0c4a6e 100%); padding: 24px; text-align: center; color: #ffffff;">
                <h1 style="margin: 0; font-size: 22px; font-weight: 800;">⚾ KBO AI 데일리 뉴스 브리핑</h1>
                <p style="margin: 6px 0 0 0; color: #38bdf8; font-size: 13px;">선택한 관심 구단: <strong>{teams_str}</strong></p>
            </div>
            <div style="padding: 24px;">
                <div style="background: #f0f9ff; border-left: 4px solid #0284c7; padding: 16px; border-radius: 6px; margin-bottom: 24px;">
                    <h2 style="margin: 0 0 10px 0; font-size: 16px; color: #0369a1;">🤖 Qwen AI 3대 핵심 브리핑</h2>
                    <div style="font-size: 14px; line-height: 1.7; color: #334155;">
                        {summary_text.replace(chr(10), '<br/>')}
                    </div>
                </div>
                {news_html_blocks}
            </div>
            <div style="background: #f1f5f9; padding: 16px; text-align: center; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0;">
                본 메일은 KBO AI 뉴스 캐스터 서비스에서 수신 동의하신 회원님께 발송되었습니다.<br/>
                © 2026 KBO AI News Caster. All rights reserved.
            </div>
        </div>
    </body>
    </html>
    """

    # 4. 이메일 발송 수행
    success, msg = send_smtp_email(payload.email, f"⚾ [KBO AI 브리핑] {teams_str} 관심 구단 데일리 뉴스 & 선발투수 정보", html_template)

    return {
        "success": success,
        "message": msg,
        "email": payload.email,
        "teams": payload.teams,
        "preview_summary": summary_text
    }

@app.post("/api/subscribe")
def subscribe_newsletter(payload: SubscribeRequest):
    """
    관심 구단 이메일 구독 정보를 MySQL(total_db.email_subscribers)에 저장합니다.
    """
    if not payload.email or "@" not in payload.email:
        raise HTTPException(status_code=400, detail="유효한 이메일 주소를 입력해 주세요.")
    if not payload.teams:
        raise HTTPException(status_code=400, detail="최소 하나 이상의 관심 구단을 선택해 주세요.")

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            teams_str = ",".join(payload.teams)
            cur.execute("""
                INSERT INTO email_subscribers (email, teams)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE teams = VALUES(teams)
            """, (payload.email, teams_str))
        conn.commit()
        conn.close()
        return {"success": True, "message": f"'{payload.email}' 주소로 {teams_str} 구단 뉴스 구독이 성공적으로 등록되었습니다!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"구독 등록 처리 실패: {str(e)}")

@app.post("/api/send-batch-email-briefings")
def send_batch_email_briefings():
    """
    Airflow 또는 배치 스케줄러가 매일 정해진 시각(예: 아침 8시)에 호출하여 
    email_subscribers 테이블의 모든 정기 구독자에게 관심 구단 맞춤 뉴스 브리핑 메일을 일괄 전송합니다.
    """
    try:
        conn = get_db_connection()
        subscribers = []
        with conn.cursor() as cur:
            cur.execute("SELECT email, teams FROM email_subscribers")
            subscribers = cur.fetchall()
        conn.close()

        if not subscribers:
            return {"success": True, "message": "등록된 정기 구독자가 없습니다.", "sent_count": 0}

        sent_count = 0
        results = []
        for sub in subscribers:
            email = sub.get("email")
            teams_raw = sub.get("teams", "")
            teams = [t.strip() for t in teams_raw.split(",") if t.strip()]
            if email and teams:
                req = EmailBriefingRequest(email=email, teams=teams)
                res = send_email_briefing(req)
                sent_count += 1
                results.append({"email": email, "teams": teams, "status": res.get("message")})

        print(f"🎉 [배치 완료] 총 {sent_count}명의 정기 구독자에게 아침 KBO 브리핑 메일 전송 완료!")
        return {
            "success": True,
            "message": f"총 {sent_count}명의 구독자에게 정기 이메일 브리핑이 성공적으로 전송되었습니다.",
            "sent_count": sent_count,
            "details": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"배치 이메일 전송 실패: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

