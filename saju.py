import os
import pymysql
import requests
from openai import OpenAI

# =========================================================================
# [1] 환경 변수(.env) 로드 헬퍼 함수
# =========================================================================
def load_env(filepath=".env"):
    if not os.path.exists(filepath):
        return
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

load_env()

# API 클라이언트 세팅 (환경 변수 우선 로드)
API_KEY = os.getenv("QWEN_API_KEY", "dcu_llm_wanhhrh13wu8eo38p98xkh06308p11hlmwdl48q8moixv1b6") 
BASE_URL = os.getenv("LLM_BASE_URL", "https://code.cu.ac.kr/llm/v1")

client = OpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
)

# =========================================================================
# KBO 구단별 대표 선발 투수 생년월일 사전 (사주 명리 정합성 확보용)
# =========================================================================
PITCHER_BIRTHDAYS = {
    "원태인": "2000년 4월 6일",
    "류현진": "1987년 3월 25일",
    "양현종": "1988년 3월 1일",
    "고영표": "1991년 9월 16일",
    "곽빈": "1999년 5월 28일",
    "임찬규": "1992년 11월 20일",
    "김광현": "1988년 7월 22일",
    "반즈": "1995년 10월 1일",
    "하트": "1992년 11월 23일",
    "후라도": "1996년 1월 30일"
}

# =========================================================================
# [2] 로컬 백업/Mock 데이터 정의 (DB 미연동 시 롤백용)
# =========================================================================
MOCK_TALENT_STATS = {
    "원태인": {"team": "삼성 라이온즈", "stuff": 115, "location": 82, "crisis_mgmt": 95},
    "류현진": {"team": "한화 이글스", "stuff": 98, "location": 125, "crisis_mgmt": 110},
    "양현종": {"team": "KIA 타이거즈", "stuff": 95, "location": 105, "crisis_mgmt": 108}
}

MOCK_NEWS_LIST = {
    "원태인": [
        {"title": "원태인, 무더위 속 체력 저하 우려... 지난 경기 4이닝 고전", "content_raw": "최근 투구 이닝이 많아 체력 소모가 다소 심한 편임"},
        {"title": "감독 왈, '원태인이 어깨 무거워 보이지만 끝까지 믿는다'", "content_raw": "구단의 전폭적인 지지를 받으나 피로도 관리가 시급"},
        {"title": "KIA 최형우, 원태인 대상 통산 5홈런 극강 천적", "content_raw": "천적 타자와의 승부가 오늘 경기의 핵심 변수"}
    ]
}

# =========================================================================
# [3] 추후 DB 연동을 완벽히 고려한 데이터 수집 모듈 (Interface)
# =========================================================================

def get_db_connection():
    """.env 설정을 기반으로 MySQL 커넥션을 반환합니다."""
    return pymysql.connect(
        host=os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASSWORD", "12341234"),
        database=os.getenv("DB_NAME", "articel_db"),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


def fetch_kbo_talent_stats(pitcher_name, opponent_team="상대팀", stadium_name="야구장", conn=None):
    """
    KBO Talent 정형 데이터베이스에서 선수의 피칭 세부 지표를 쿼리합니다.
    """
    # 1) 기본 롤백 데이터 설정
    stats = MOCK_TALENT_STATS.get(pitcher_name, {"team": "KBO 구단", "stuff": 100, "location": 100, "crisis_mgmt": 100})
    stats["opponent"] = opponent_team  # 경기 매치업에 따른 실제 상대팀 정보 연동
    stats["stadium"] = stadium_name  # 실제 경기 장소 연동
    
    if not conn:
        return stats
        
    try:
        with conn.cursor() as cursor:
            # 🔮 [추후 정형 DB 테이블 연결 시 아래 SQL 활용]
            # SQL 예시:
            # sql = """
            #     SELECT team, stuff_plus AS stuff, location_plus AS location, oswc AS crisis_mgmt, opponent, stadium
            #     FROM kbo_talent_stats 
            #     WHERE pitcher_name = %s
            #     LIMIT 1
            # """
            # cursor.execute(sql, (pitcher_name,))
            # db_result = cursor.fetchone()
            # if db_result:
            #     stats = db_result
            pass
    except Exception as e:
        print(f"⚠️ KBO Talent 스탯 조회 실패 (백업용 로컬 데이터 사용): {e}")
        
    return stats


def fetch_daum_news_summary(pitcher_name, conn):
    """
    다음 기사 크롤링 DB 테이블(news_articles)에서 선수의 최근 뉴스 정보를 쿼리합니다.
    """
    # 1) 기본 롤백 데이터 설정
    news_list = MOCK_NEWS_LIST.get(pitcher_name, [
        {"title": f"{pitcher_name}, 선발 출격 준비 완료", "content_raw": "구위 및 당일 컨디션 조율 집중"},
        {"title": f"타선의 지원 여부가 {pitcher_name}의 승리 여부를 결정할 것", "content_raw": "득점권 타선의 적절한 타격 지원 필요"}
    ])
    
    if not conn:
        return news_list
        
    try:
        with conn.cursor() as cursor:
            # 실시간 수집된 news_articles 테이블 활용
            sql = """
                SELECT title, content 
                FROM news_articles 
                WHERE (title LIKE %s OR content LIKE %s)
                ORDER BY date DESC 
                LIMIT 3
            """
            search_param = f"%{pitcher_name}%"
            cursor.execute(sql, (search_param, search_param))
            db_news = cursor.fetchall()
            
            if db_news:
                # content 컬럼 데이터를 content_raw 키값에 바인딩
                news_list = []
                for row in db_news:
                    news_list.append({
                        "title": row["title"],
                        "content_raw": row["content"]
                    })
                print(f"📰 DB에서 '{pitcher_name}' 관련 다음 크롤링 기사 {len(db_news)}건을 동적으로 로드했습니다.")
    except Exception as e:
        print(f"⚠️ 다음 크롤링 뉴스 조회 실패 (백업용 로컬 뉴스 사용): {e}")
        
    return news_list


# =========================================================================
# [4] 메인 사주 생성 로직 및 시스템 프롬프트
# =========================================================================

system_instruction = """
너는 KBO 프로야구 데이터, 선수의 생년월일 정보, 그리고 다음(daum.net) 뉴스 기사를 융합하여 오늘 경기 선발 투수의 운세를 점치는 40년 경력의 신비롭고 영험한 야구 역술인 '야잘알 도사'이다.

[사주 명리 가이드 (컴투스온 개발자 가이드 준수)]
1. **객관성 확보 및 편향 배제**: 사용자의 기대치나 유도 질문(예: 특정 팀의 편을 들거나 특정 결과를 유도하는 뉘앙스)에 휘둘리지 말고, 제공된 생년월일과 팩트 스탯에 기반하여 냉철하고 엄정하게 명리학적 밸런스를 감정하라.
2. **구체적 수치(승패/스코어)의 맹신 방지**: "오늘 반드시 8이닝 무실점 완봉승을 한다"는 식의 허황되고 확정적인 미래 예측 수치는 명리학적으로 맞지 않으며 AI의 한계이므로 삼가라. 마운드 위에서 흐를 기운의 흐름과 조율하는 전략적 방향성(액막이, 경기 조율 타이밍) 위주로 조언하라.
3. **타고난 기질 및 오행 밸런스 분석**: KBO Talent 스탯을 동양 철학의 오행(五行)으로 정밀하게 비유하여 풀어라:
   - 순수 구위(Stuff) -> 화(火)의 기운 (마운드를 녹일 듯한 맹렬한 불꽃 기세)
   - 제구력(Location) -> 금(金)의 기운 (칼날처럼 차갑고 예리한 쇠붙이의 통제력)
   - 위기상황 담력(Crisis Mgmt) -> 토(土)의 기운 (풍파에도 흔들리지 않는 태산의 우직함)
   이 3가지 오행의 생극제화(生剋制化) 균형을 바탕으로 투수의 타고난 그날의 멘탈적/피지컬적 강점을 도출하라.
4. **상극(相剋) 살풀이**: 뉴스 기사에 나타난 라이벌/천적 타자와의 매치업을 오행의 상극 관계(예: 목극토 木剋土, 화극금 火剋金)로 해석하여 어느 타석에 큰 액운이 끼어 있는지 짚고 대처법을 권하라.

[말투 가이드]
1. 무속인 특유의 고풍스럽고 엄숙한 말투("~이로다", "~하구나", "상극일세")를 쓰되, 야구 커뮤니티의 밈과 매운맛 드립을 한두 스푼 가미하라.
2. 마운드 위에서 펼쳐질 투쟁과 피칭의 흐름을 한 폭의 사주 신선도처럼 시각적이고 웅장하게 서술하라.

[출력 형식]
반드시 다음 구조로만 출력하고, 마크다운(Markdown) 예쁜 양식으로 가독성 좋게 꾸며라. 사족은 절대 붙이지 마라.
# 🔮 [선수이름] 오늘의 야구 사주풀이
## 1. 👁️ 오늘 선발의 운세 총평
## 2. ☯️ 데이터로 보는 오행의 기운 (KBO Talent 해석)
## 3. ⚠️ 오늘 피해야 할 액운과 상극(相剋) 타자 (뉴스 기사 기반)
## 4. 🧧 팬들을 위한 행운의 관전 비책
"""

def get_baseball_saju(pitcher_name, opponent_team="상대팀", stadium_name="야구장", game_date=None):
    # 1) 경기 일자(오늘의 일진 날짜) 기본값 세팅
    import datetime
    if not game_date:
        game_date = datetime.date.today().strftime("%Y-%m-%d")

    # 2) 데이터베이스 커넥션 생성 시도
    conn = None
    try:
        conn = get_db_connection()
    except Exception as e:
        print(f"⚠️ DB 연결 비활성화 또는 설정 정보 오류 (로컬 백업 모드 실행): {e}")

    # 3) 각 데이터 소스별 모듈을 통한 개별 수집 (매치업 상대팀 및 구장 정보 공급)
    stats = fetch_kbo_talent_stats(pitcher_name, opponent_team, stadium_name, conn)
    news_list = fetch_daum_news_summary(pitcher_name, conn)
    
    # DB 사용 완료 후 종료
    if conn:
        conn.close()

    # 4) 동적 LLM 주입 텍스트 조립
    news_text = ""
    for idx, news in enumerate(news_list, 1):
        news_text += f"{idx}. 제목: {news['title']}\n   내용: {news['content_raw'][:150]}...\n"

    # 투수의 생년월일 매핑
    birthday = PITCHER_BIRTHDAYS.get(pitcher_name, "알 수 없음 (도사의 혜안으로 사주 추출)")

    user_prompt = f"""
[오늘의 선발 투수 정보]
- 이름: {pitcher_name}
- 생년월일 (사주 풀이용): {birthday}
- 경기 일자 (오늘의 일진 판별용): {game_date}
- 소속 팀: {stats['team']}
- 매치업: vs {stats.get('opponent', '상대팀')} ({stats.get('stadium', '야구장')})

[정형 데이터: KBO Talent 스탯]
- K-Stuff+ (순수 구위): {stats['stuff']} (100 기준)
- K-Location+ (제구력): {stats['location']} (100 기준)
- FCB.OSWC (위기 상황 담력): {stats['crisis_mgmt']} (100 기준)

[비정형 데이터: 최근 다음 뉴스 기사 요약]
{news_text}
"""

    print("🔮 야잘알 도사가 엽전을 던져 운세를 보고 있습니다... 잠시만 기다리시게...\n")
    
    # system_instruction을 user 롤 프롬프트 상단에 강결합하여 전달 (API 제약 대응)
    full_prompt = f"""{system_instruction}

위 지침을 엄격히 준수하여 아래 제공되는 오늘 선발 투수의 운세를 명리학에 기반해 상세히 점쳐 주시오.

{user_prompt}"""

    url = "https://code.cu.ac.kr/llm/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    data = {
        "model": "Qwen/Qwen3.5-35B-A3B-FP8",
        "messages": [
            {"role": "user", "content": full_prompt}
        ],
        "stream": False
    }
    
    try:
        resp = requests.post(url, headers=headers, json=data)
        resp.raise_for_status()
        resp_data = resp.json()
        choices = resp_data.get("choices", [])
        if not choices:
            return "❌ 도사님이 오늘 점괘를 내지 못하시는구나. (LLM 응답 비어있음)"
        content = choices[0].get("message", {}).get("content")
        saju_result = content.strip() if content else "❌ 도사님이 오늘 점괘를 내지 못하시는구나. (content 비어있음)"
        return saju_result
    except Exception as e:
        err_msg = f"❌ 액운이 끼어 API 호출에 실패했구나!: {e}"
        print(err_msg)
        return err_msg

if __name__ == "__main__":
    # 단독 테스트를 위해 결과를 콘솔에 출력합니다.
    print(get_baseball_saju("원태인"))
