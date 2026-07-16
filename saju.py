import os
import sys
import pymysql
import requests
from openai import OpenAI

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 딥러닝 강화 모듈 (Phase 1: 감성분석 / Phase 3: RAG 명리학) ──────────────
try:
    from deep_learning.saju_sentiment import analyze_news_sentiment
    SENTIMENT_OK = True
except ImportError:
    SENTIMENT_OK = False

try:
    from deep_learning.saju_rag import get_saju_pillar, query_rag, init_knowledge_base
    RAG_OK = True
except ImportError:
    RAG_OK = False

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
        database=os.getenv("DB_NAME", "total_db"),
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

# ── RAG 지식베이스 초기화 (모듈 로드 시 1회 실행) ────────────────────────────
if RAG_OK:
    try:
        init_knowledge_base()   # MySQL에 명리학 지식 없으면 자동 삽입
    except Exception:
        pass


def get_pitcher_dl_prediction(pitcher_name):
    """
    statistics_db에서 훈련 피처와 모델을 로드하여 딥러닝 예측값(ERA, Ace 여부)을 반환합니다.
    """
    import os
    import pymysql
    import pickle
    import json
    import numpy as np

    try:
        conn = pymysql.connect(
            host=os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", "admin"),
            password=os.getenv("DB_PASSWORD", "12341234"),
            database="statistics_db",
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5
        )
    except Exception as e:
        print(f"[DL-Inference] DB 연결 실패 (오류: {e})")
        return None

    try:
        with conn.cursor() as cursor:
            # 2. 선수의 최신 피처 조회
            feature_cols = ["era", "whip", "so_per_9", "bb_per_9", "hr_per_9", "k_bb_ratio", "era_z", "opp_ops", "opp_avg"]
            sql_features = f"""
                SELECT {', '.join(feature_cols)}
                FROM training_features
                WHERE player_name = %s
                ORDER BY season DESC
                LIMIT 1
            """
            cursor.execute(sql_features, (pitcher_name,))
            feat_row = cursor.fetchone()
            if not feat_row:
                print(f"[DL-Inference] '{pitcher_name}' 선수의 피처 정보가 없습니다.")
                return None

            # 3. 최신 모델 가중치 조회
            sql_models = """
                SELECT model_name, weights, scaler, feature_cols
                FROM model_weights
                WHERE model_name IN ('saju_era_regressor', 'saju_ace_classifier')
                ORDER BY trained_at DESC
                LIMIT 2
            """
            cursor.execute(sql_models)
            model_rows = cursor.fetchall()
            if not model_rows:
                print("[DL-Inference] 학습된 모델 가중치(model_weights)가 존재하지 않습니다.")
                return None

            # 모델 딕셔너리 구축
            models_dict = {row["model_name"]: row for row in model_rows}
            reg_row = models_dict.get("saju_era_regressor")
            cls_row = models_dict.get("saju_ace_classifier")

            if not reg_row and not cls_row:
                print("[DL-Inference] 레그레서 혹은 분류기 가중치가 누락되었습니다.")
                return None

            # 4. 피처 벡터 구성
            x_input = [float(feat_row[c]) if feat_row[c] is not None else 0.0 for c in feature_cols]
            X = np.array([x_input], dtype=np.float32)

            predicted_era = None
            predicted_ace = None

            # 5.1 Regressor (ERA 예측)
            if reg_row:
                scaler = pickle.loads(reg_row["scaler"])
                model = pickle.loads(reg_row["weights"])
                X_scaled = scaler.transform(X)
                pred_val = model.predict(X_scaled)
                if hasattr(pred_val, "flatten"):
                    pred_val = pred_val.flatten()
                predicted_era = float(pred_val[0])

            # 5.2 Classifier (Ace 여부 분류)
            if cls_row:
                scaler_cls = pickle.loads(cls_row["scaler"])
                model_cls = pickle.loads(cls_row["weights"])
                X_scaled_cls = scaler_cls.transform(X)
                pred_cls = model_cls.predict(X_scaled_cls)
                if hasattr(pred_cls, "flatten"):
                    pred_cls = pred_cls.flatten()
                predicted_ace = int(pred_cls[0])

            return {
                "predicted_era": predicted_era,
                "predicted_ace": predicted_ace
            }

    except Exception as e:
        print(f"[DL-Inference] 예측 과정 중 오류 발생 (라이브러리 미설치 등): {e}")
        return None
    finally:
        conn.close()


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

    # ── Phase 1: 뉴스 감성 분석 ─────────────────────────────────────────────
    sentiment_text = ""
    if SENTIMENT_OK:
        try:
            sentiment = analyze_news_sentiment(news_list)
            momentum  = sentiment.get("momentum", "중립기운")
            neg_pct   = int(sentiment.get("negative", 0) * 100)
            pos_pct   = int(sentiment.get("positive", 0) * 100)
            ohaeng    = sentiment.get("ohaeng_hint", "")
            signals   = ", ".join(sentiment.get("key_signals", [])[:3])
            sentiment_text = (
                f"\n[📊 뉴스 감성 분석 결과 (KoBERT)]\n"
                f"- 긍정 {pos_pct}% / 부정 {neg_pct}%\n"
                f"- 기운 판정: {momentum}\n"
                f"- 오행 해석: {ohaeng}\n"
                f"- 핵심 신호: {signals or '없음'}"
            )
        except Exception as e:
            print(f"⚠️ 감성 분석 실패 (무시): {e}")

    # ── Phase 3: RAG 명리학 지식 검색 ────────────────────────────────────────
    rag_text = ""
    if RAG_OK:
        try:
            birthday = PITCHER_BIRTHDAYS.get(pitcher_name, "")
            saju_pillar = get_saju_pillar(birthday) if birthday else {}
            rag_text = query_rag(pitcher_name, saju_pillar, stats, top_k=4)
        except Exception as e:
            print(f"⚠️ RAG 쿼리 실패 (무시): {e}")

    # ── Phase 4: 딥러닝 모델 예측치 로드 ──────────────────────────────────────
    dl_prediction = None
    try:
        dl_prediction = get_pitcher_dl_prediction(pitcher_name)
    except Exception as e:
        print(f"⚠️ 딥러닝 예측 로드 실패: {e}")

    dl_text = ""
    if dl_prediction:
        pred_era = dl_prediction.get("predicted_era")
        pred_ace = dl_prediction.get("predicted_ace")
        dl_text = "\n[📊 딥러닝 예측 데이터 (TabNet 모델 결과)]\n"
        if pred_era is not None:
            dl_text += f"- 예측 다음 시즌 평균자책점(ERA): {pred_era:.2f}\n"
        if pred_ace is not None:
            ace_status = "에이스 등극 가능 (평균자책점 3.5 미만)" if pred_ace == 1 else "보통 선발 수준"
            dl_text += f"- 예측 에이스(Ace) 여부: {ace_status}\n"

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
{news_text}{sentiment_text}
{dl_text}
{rag_text}
"""

    dl_status = []
    if SENTIMENT_OK: dl_status.append("감성분석✅")
    if RAG_OK:       dl_status.append("RAG명리✅")
    if dl_prediction: dl_status.append("TabNet딥러닝✅")
    mode_str = " | ".join(dl_status) if dl_status else "기본모드"
    print(f"🔮 야잘알 도사가 엽전을 던져 운세를 보고 있습니다... [{mode_str}] 잠시만 기다리시게...\n")

    # system_instruction을 user 롤 프롬프트 상단에 강결합하여 전달 (API 제약 대응)
    full_prompt = f"""{system_instruction}

위 지침을 엄격히 준수하여 아래 제공되는 오늘 선발 투수의 운세를 명리학에 기반해 상세히 점쳐 주시오.
제공된 [RAG 명리학 지식베이스]와 [뉴스 감성 분석 결과]를 반드시 사주 해석의 근거로 활용하라.
특히, [딥러닝 예측 데이터]가 제공되는 경우 이 수치(예상 ERA 및 에이스 승격 여부)를 언급하여 미래 마운드 위 기운의 향방에 신빙성을 보태시오.

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
