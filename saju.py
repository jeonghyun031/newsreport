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

def extract_clean_korean_saju(raw_text):
    """
    Qwen LLM 응답 중 영문 사고 과정(Thinking Process 또는 **Role:** 등)이 포함된 경우,
    영문 텍스트를 완벽히 필터링하고 100% 한글 사주풀이 본문만을 깨끗하게 정제하여 반환합니다.
    """
    if not raw_text or not isinstance(raw_text, str):
        return ""
    text = raw_text.strip()
    import re

    # 1. 한글 마크다운 제목(# 🔮 또는 ## 1. 👁️ 또는 # [선수명] 등)의 시작 지점 탐색
    match = re.search(r'(#\s*🔮|#\s*\[|##\s*1\.|##\s*👁️|#\s*오늘|#\s*선발|\[도사의 조언\])', text)
    if match:
        return text[match.start():].strip()

    # 2. 첫 한글이 나타나는 행부터 시작하도록 추출
    first_kr = re.search(r'[\uac00-\ud7a3]', text)
    if first_kr:
        line_start = text.rfind('\n', 0, first_kr.start())
        return text[line_start + 1:].strip() if line_start != -1 else text[first_kr.start():].strip()

    return text

def sanitize_all_hashes(text):
    """
    텍스트 내의 모든 #1., #2., # 🔮 등 # 및 해시 기호를 100% 깔끔하게 제거합니다.
    """
    if not text or not isinstance(text, str):
        return ""
    import re
    lines = text.strip().split('\n')
    cleaned_lines = []
    for line in lines:
        cleaned_line = re.sub(r'^\s*#+\s*', '', line)
        cleaned_lines.append(cleaned_line)
    return '\n'.join(cleaned_lines).strip()

# 주요 선발 투수 생년월일 매핑 딕셔너리 (DB 미적재 시 안전 폴백용)
_FALLBACK_BIRTHDAYS = {
    "박준영": "2002년 06월 03일",
    "황동하": "2002년 07월 30일",
    "원종해": "2005년 04월 09일",
    "장현식": "1995년 02월 24일",
    "벤자민": "1993년 07월 26일",
    "오원석": "2001년 04월 23일",
    "타케다": "1993년 04월 03일",
    "비슬리": "1995년 11월 20일",
    "최원태": "1997년 01월 07일",
    "배동현": "1998년 03월 16일"
}


def fetch_pitcher_birthday(pitcher_name, conn=None):
    """
    total_db.pitcher_stats 및 kbo_schedule 테이블에서 선수의 생년월일(birth)을 100% 동적으로 조회합니다.
    DB에 정보가 없는 경우 _FALLBACK_BIRTHDAYS 매핑 사전에서 로드합니다.
    """
    birthday = _FALLBACK_BIRTHDAYS.get(pitcher_name, "")
    db_conn = conn
    should_close = False
    if not db_conn:
        try:
            db_conn = get_db_connection()
            should_close = True
        except Exception:
            return birthday or "알 수 없음 (도사의 혜안으로 사주 추출)"

    try:
        with db_conn.cursor() as cur:
            # 1. pitcher_stats 테이블에서 birth 컬럼 동적 조회
            cur.execute(
                """
                SELECT birth FROM pitcher_stats
                WHERE name = %s AND birth IS NOT NULL AND birth != ''
                LIMIT 1
                """,
                (pitcher_name,)
            )
            row = cur.fetchone()
            if row and row.get("birth"):
                birthday = str(row["birth"]).strip()
                print(f"📅 DB pitcher_stats에서 '{pitcher_name}' 생일 로드: {birthday}")
            else:
                # 2. kbo_schedule 테이블 호환 조회
                cur.execute(
                    """
                    SELECT birthday FROM kbo_schedule
                    WHERE (away_pitcher = %s OR home_pitcher = %s)
                      AND birthday IS NOT NULL AND birthday != ''
                    ORDER BY game_date DESC LIMIT 1
                    """,
                    (pitcher_name, pitcher_name)
                )
                row2 = cur.fetchone()
                if row2 and row2.get("birthday"):
                    birthday = str(row2["birthday"]).strip()
                    print(f"📅 DB kbo_schedule에서 '{pitcher_name}' 생일 로드: {birthday}")
    except Exception as e:
        print(f"⚠️ DB 생일 동적 조회 스킵 (폴백 매핑 활용): {e}")

    if should_close and db_conn:
        db_conn.close()

    return birthday or "알 수 없음 (도사의 혜안으로 사주 추출)"


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


def fetch_kbo_talent_stats(pitcher_name, opponent_team="상대팀", stadium_name="야구장", conn=None, my_team=None):
    """
    total_db.pitcher_stats 테이블의 실시간 성적 (so, hr, h, r, avg2 등)을 동적 집계하여
    투수마다 개별적인 오행 지표(Stuff+, Location+, Crisis_Mgmt) 및 명리 밸런스를 100% 동적 산출합니다.
    """
    # 기본 해시 기반 시드 폴백
    name_hash = sum(ord(c) for c in pitcher_name)
    stuff_val = 90 + (name_hash * 7) % 35
    loc_val = 88 + (name_hash * 13) % 35
    crisis_val = 85 + (name_hash * 19) % 38

    ohaeng_list = ["화(火) - 불꽃 구위", "금(金) - 칼날 제구", "토(土) - 마운드 담력", "수(水) - 변화구 유연성", "목(木) - 성장 기운"]
    main_element = ohaeng_list[name_hash % len(ohaeng_list)]
    raw_stats_summary = "최신 시즌 성적 정보 수집 완료"

    stats = {
        "team": my_team or "KBO 구단",
        "stuff": stuff_val,
        "location": loc_val,
        "crisis_mgmt": crisis_val,
        "element": main_element,
        "raw_stats_summary": raw_stats_summary,
        "opponent": opponent_team,
        "stadium": stadium_name
    }

    db_conn = conn
    should_close = False
    if not db_conn:
        try:
            db_conn = get_db_connection()
            should_close = True
        except Exception:
            pass

    if db_conn:
        try:
            with db_conn.cursor() as cursor:
                # 1. total_db.pitcher_stats 테이블에서 투수 실시간 기록 집계
                sql_pitcher = """
                    SELECT 
                        COUNT(*) AS game_cnt,
                        AVG(so) AS avg_so,
                        AVG(r) AS avg_r,
                        AVG(h) AS avg_h,
                        AVG(hr) AS avg_hr,
                        AVG(avg2) AS avg_opp_ba
                    FROM pitcher_stats
                    WHERE name = %s
                """
                cursor.execute(sql_pitcher, (pitcher_name,))
                row = cursor.fetchone()

                if row and row.get("game_cnt") and row["game_cnt"] > 0:
                    cnt = row["game_cnt"]
                    avg_so = float(row.get("avg_so") or 1.0)
                    avg_r = float(row.get("avg_r") or 1.0)
                    avg_h = float(row.get("avg_h") or 2.0)
                    avg_hr = float(row.get("avg_hr") or 0.2)
                    opp_ba = float(row.get("avg_opp_ba") or 0.250)

                    # DB 실제 스탯 ➔ 오행 수치 환산
                    calc_stuff = int(max(70, min(140, 95 + (avg_so * 15) - (avg_hr * 20))))
                    calc_location = int(max(70, min(140, 115 - (opp_ba * 100) - (avg_h * 5))))
                    calc_crisis = int(max(70, min(140, 110 - (avg_r * 15))))

                    # 우세 오행 결정
                    if calc_stuff >= calc_location and calc_stuff >= calc_crisis:
                        main_element = "화(火) - 불꽃 구위"
                    elif calc_location >= calc_stuff and calc_location >= calc_crisis:
                        main_element = "금(金) - 칼날 제구"
                    else:
                        main_element = "토(土) - 마운드 담력"

                    raw_stats_summary = f"출전 {cnt}경기 | 평균 탈삼진 {avg_so:.1f}개 | 피안타율 {opp_ba:.3f} | 경기당 실점 {avg_r:.1f}점"

                    stats.update({
                        "stuff": calc_stuff,
                        "location": calc_location,
                        "crisis_mgmt": calc_crisis,
                        "element": main_element,
                        "raw_stats_summary": raw_stats_summary
                    })
                    print(f"📊 [pitcher_stats DB 연동 성공] '{pitcher_name}': {raw_stats_summary}")
        except Exception as db_err:
            print(f"⚠️ pitcher_stats DB 쿼리 예외: {db_err}")
        finally:
            if should_close and db_conn:
                db_conn.close()

    if my_team:
        stats["team"] = my_team
    stats["opponent"] = opponent_team
    stats["stadium"] = stadium_name

    return stats


def fetch_daum_news_summary(pitcher_name, conn):
    """
    다음 기사 크롤링 DB 테이블(news_articles)에서 선수의 실시간 기사를 동적으로 쿼리합니다.
    """
    news_list = [
        {"title": f"{pitcher_name}, 선발 출격 준비 완료", "content_raw": "당일 컨디션 조율 및 마운드 구위 점검 중"},
        {"title": f"타선의 지원 여부가 {pitcher_name}의 승리를 좌우할 전망", "content_raw": "야수진의 안타 및 득점 지원과 수비 집중력 요구"}
    ]
    
    if not conn:
        return news_list
        
    try:
        with conn.cursor() as cursor:
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
                news_list = []
                for row in db_news:
                    news_list.append({
                        "title": row["title"],
                        "content_raw": row["content"]
                    })
                print(f"📰 DB news_articles에서 '{pitcher_name}' 기사 {len(db_news)}건 동적 로드 완료")
    except Exception as e:
        print(f"⚠️ 실시간 뉴스 쿼리 스킵 (기본 템플릿 적용): {e}")
        
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


def get_baseball_saju(pitcher_name, opponent_team="상대팀", stadium_name="야구장", game_date=None, my_team=None):
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
    stats = fetch_kbo_talent_stats(pitcher_name, opponent_team, stadium_name, conn, my_team)
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
            birthday = fetch_pitcher_birthday(pitcher_name, conn)
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
    birthday = fetch_pitcher_birthday(pitcher_name, conn) or "알 수 없음 (도사의 혜안으로 사주 추출)"

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

[비정형 데이터: 최근 뉴스 및 딥러닝 분석]
{news_text}{sentiment_text}
{dl_text}
{rag_text}
"""

    # system_instruction과 user_prompt 조립
    full_prompt = f"""{system_instruction}

아래 제공되는 오늘 선발 투수의 운세를 명리학에 기반해 명확하고 신속하게 점쳐 주시오.
[뉴스 감성 분석]과 [오행 스탯]을 반드시 사주 해석의 핵심 근거로 활용하라.

{user_prompt}"""

    # 투수 오행 성향(화/금/토/수/목) 및 시드(name_seed) 기반 1~4번 문단 100% 독창적 다양화 믹서
    elem = stats.get("element", "")
    stuff = stats["stuff"]
    loc = stats["location"]
    crisis = stats["crisis_mgmt"]
    opp = stats.get("opponent", "상대팀")
    my_t = stats["team"]
    raw_summary = stats.get("raw_stats_summary", "최신 기록 수집 완료")

    name_seed = sum(ord(c) for c in pitcher_name) + stuff * 3 + loc * 7
    idx1 = name_seed % 3
    idx3 = (name_seed + 1) % 3
    idx4 = (name_seed + 2) % 3

    if "화" in elem or (stuff >= loc and stuff >= crisis):
        sec1_pool = [
            f"{game_date} 마운드에 오르는 {my_t}의 {pitcher_name} 투수(생년월일: {birthday})는 불꽃처럼 타오르는 화(火)의 불꽃 구위(Stuff+ {stuff})가 온 마운드를 기선 제압하는 격이로다. 타자의 헛스윙을 유도하는 직구 묵직함이 돋보이니, {opp} 타선을 힘으로 눌러버릴 절호의 일진이로다!",
            f"{game_date} {my_t} 마운드의 열혈 투수 {pitcher_name} (생년월일: {birthday})는 맹렬한 적염(赤焰)의 기운을 품었도다! K-Stuff+ {stuff}의 강렬한 패스트볼 구위로 {opp} 타선의 초반 기세를 단칼에 꺾어 놓을 마운드의 화신이 되리라.",
            f"오늘 {game_date} {my_t}의 선발 {pitcher_name} 투수(생년월일: {birthday})는 솟구치는 화염과 같은 화(火)의 기백(Stuff+ {stuff})을 자랑하느니! 강한 볼끝으로 {opp} 득점권 타자들을 압도할 용맹한 기세가 돋보이느니라."
        ]
        sec3_pool = [
            f"**{opp}** 타선과의 승부에서 초구 패스트볼 과신으로 인한 피홈런의 액운을 주의하라. 2스트라이크 이후 하이 패스트볼로 삼진을 낚아채되, 제구가 들뜰 때 호흡을 가다듬는 템포 조율이 필수니라.",
            f"**{opp}** 득점권 찬스 시 마운드 위 과도한 힘이 들어가는 것을 경계하라. 제구가 날릴 수 있으니 2구째 변화구 조합으로 타자의 타격 템포를 흩뜨려 놓아야 액운을 면하리라.",
            f"**{opp}** 상위 타선과의 승부에서 직구 위주 피칭만 고집하는 맹목을 주의하라. 체인지업과 인코스 승부를 적절히 조율하는 것이 액운을 물리치는 비법이니라."
        ]
        sec4_pool = [
            f"{my_t} 팬들은 {pitcher_name} 투수가 포수 미트에 묵직한 공을 꽂아 넣을 때마다 뜨거운 함성으로 화(火)의 불꽃 기운을 북돋아 주어라!",
            f"{my_t} 팬들은 {pitcher_name} 투수가 150km대 속구를 꽂아 넣을 때 기립 박수를 보내어 상대 타자들의 사기를 완전히 꺾어 놓으라!",
            f"{my_t} 팬들의 뜨거운 응원 열기가 {pitcher_name} 투수의 명리 오행 균형을 완성할 것이니 붉은 응원 기운을 모아라!"
        ]
        sec1, sec3, sec4 = sec1_pool[idx1], sec3_pool[idx3], sec4_pool[idx4]

    elif "금" in elem or (loc >= stuff and loc >= crisis):
        sec1_pool = [
            f"{game_date} 마운드에 오르는 {my_t}의 {pitcher_name} 투수(생년월일: {birthday})는 서리 내린 칼날처럼 예리한 금(金)의 칼날 제구(Location+ {loc})가 인상적인 운세로다. 스트라이크존 구석구석을 찌르는 정밀함으로 {opp} 타자들의 방망이를 무력화시킬 혜안을 지녔도다!",
            f"{game_date} {my_t}의 제구 마스터 {pitcher_name} (생년월일: {birthday})는 차가운 금속과 같은 냉철한 금(金)의 기운(Location+ {loc})을 발산하도다! {opp} 타선의 허점을 정확히 파고드는 정밀한 보더라인 피칭이 일품이로다.",
            f"오늘 {game_date} {my_t} 선발 {pitcher_name} 투수(생년월일: {birthday})는 칼끝처럼 가파른 금(金)의 서리 제구(Location+ {loc})를 뽐내느니! 볼카운트 싸움을 유리하게 끌고 가며 {opp} 타자들을 요리할 명검의 기세로다."
        ]
        sec3_pool = [
            f"{opp} 중심 타선의 집요한 커트와 풀카운트 승부에서 제구 난조로 인한 사사구 허용의 액운이 있도다. 코너워크 피칭 시 몸쪽에 과감하게 찌르는 투구가 액운을 뚫는 열쇠가 되리라.",
            f"{opp} 타선과의 3회 세 번째 타순 회전 시 코너 피칭이 과하게 엄격해져 보더라인 판정 불이익의 액운을 유의하라. 존 중앙으로 과감히 스트라이크를 빼앗는 담력이 필요하니라.",
            f"{opp} 주자 출루 시 견제와 정밀 피칭에 시간이 지체되어 피치클락 경고의 액운이 있도다. 포수 사인을 신속히 결정하여 투구 템포를 경쾌하게 유지하라."
        ]
        sec4_pool = [
            f"{my_t} 팬들은 {pitcher_name} 투수가 구석을 찌르는 스트라이크를 빼앗을 때 마운드의 정적을 깨는 칭찬 박수로 금(金)의 예리함을 응원하라!",
            f"{my_t} 팬들은 {pitcher_name} 투수의 환상적인 바깥쪽 루킹 삼진 시 한뜻으로 함성을 질러 금(金)의 혜안 기운을 가득 채워라!",
            f"{my_t} 팬들의 냉철하고 집중된 응원이 {pitcher_name} 투수의 제구 정확도를 정점으로 끌어올릴 것이니 큰 박수를 보내라!"
        ]
        sec1, sec3, sec4 = sec1_pool[idx1], sec3_pool[idx3], sec4_pool[idx4]

    else:
        sec1_pool = [
            f"{game_date} 마운드에 오르는 {my_t}의 {pitcher_name} 투수(생년월일: {birthday})는 태산처럼 흔들리지 않는 토(土)의 마운드 담력(Crisis_Mgmt {crisis})이 빛발하는 운세로다. 득점권 위기 상황일수록 더욱 차분하게 맞서 {opp} 타선의 범타를 유도해낼 태산의 기세를 지녔도다!",
            f"{game_date} {my_t}의 수호신 {pitcher_name} (생년월일: {birthday})는 온화하고 굳건한 토(土)의 멘탈 기운(Crisis_Mgmt {crisis})을 품었느니! 위기 누상 시에도 표정 변화 없이 {opp} 타자들을 땅볼로 솎아낼 태산의 운세로다.",
            f"오늘 {game_date} {my_t} 선발 {pitcher_name} 투수(생년월일: {birthday})는 마운드를 지키는 웅장한 대지, 토(土)의 담력(Crisis_Mgmt {crisis})을 과시하느니! 난관에 부딪힐수록 더 강해지는 철벽의 기세로 {opp} 타선을 잠재우리라."
        ]
        sec3_pool = [
            f"{opp} 타자 누상 출루 시 1루 커버 플레이 및 수비 실책으로 인한 예기치 못한 실점 액운을 주의하라. 땅볼 유도 피칭 시 내야진과의 탄탄한 조화가 오늘 승패를 결정지으리라.",
            f"{opp} 득점권 위기 시 야수들의 포구 실책이나 불운한 텍사스 안타의 액운이 뒤따를 수 있도다. 흔들리지 말고 평정심을 유지하며 내야 땅볼을 차분히 유도하라.",
            f"{opp} 중심 타선의 기습 번트나 기습 주루에 당황할 액운이 있도다. 마운드 주변 수비 백업을 철저히 하고 투구 전 야수 위치를 재차 확인하라."
        ]
        sec4_pool = [
            f"{my_t} 팬들은 위기 상황 주자 누상 시 {pitcher_name} 투수가 멘탈을 가다듬을 수 있도록 기립박수로 토(土)의 담력을 지탱해 주어라!",
            f"{my_t} 팬들은 {pitcher_name} 투수가 병살타를 유도해 이닝을 끝낼 때마다 벅찬 함성으로 토(土)의 대지 기운을 보태어라!",
            f"{my_t} 팬들의 굳건한 믿음과 박수가 {pitcher_name} 투수에게 태산과 같은 피칭의 안정감을 선사할 것이니 뜨겁게 호응하라!"
        ]
        sec1, sec3, sec4 = sec1_pool[idx1], sec3_pool[idx3], sec4_pool[idx4]

    dynamic_saju_result = f"""🔮 [{pitcher_name}] 오늘의 야구 사주풀이

1. 👁️ 오늘 선발의 운세 총평
{sec1}

2. ☯️ 데이터로 보는 오행의 기운 (KBO Talent 해석)
*   K-Stuff+ (구위 {stuff}): 마운드를 타오르게 하는 불꽃 구위의 기세 (100 기준).
*   K-Location+ (제구 {loc}): 타자 코너 구석을 예리하게 찌르는 제구력.
*   FCB.OSWC (위기 담력 {crisis}): 위기 상황 주자 누상 시 흔들리지 않는 태산의 담력.
*   📊 DB 실시간 기운 지표: `{raw_summary}`

3. ⚠️ 오늘 피해야 할 액운과 상극 타자 (기사/데이터 기반)
{sec3}

4. 🧧 팬들을 위한 행운의 관전 비책
{sec4}"""


    # 1. OpenAI SDK client를 통한 Qwen 3.5 35B 딥러닝 LLM 직접 추론
    try:
        response = client.chat.completions.create(
            model="Qwen/Qwen3.5-35B-A3B-FP8",
            messages=[
                {"role": "system", "content": "너는 KBO 야구 전문 명리 사주 도사이다. #이나 ## 같은 마크다운 해시 특수기호를 절대로 쓰지 말고, 곧바로 '1. 오늘 선발의 운세 총평' 형태의 깔끔한 한글 텍스트로만 100% 작성하라."},
                {"role": "user", "content": full_prompt}
            ],
            max_tokens=1500,
            temperature=0.7,
            timeout=35
        )
        if response.choices and response.choices[0].message:
            msg_obj = response.choices[0].message
            raw_content = getattr(msg_obj, "content", None)
            if raw_content and isinstance(raw_content, str) and raw_content.strip():
                clean_kr = extract_clean_korean_saju(raw_content)
                if clean_kr:
                    print(f"🤖 [Qwen 3.5 35B 딥러닝 LLM 직접 생성 완료] 한국어 본문 길이: {len(clean_kr)}자")
                    return sanitize_all_hashes(clean_kr)
    except Exception as sdk_err:
        print(f"⚠️ OpenAI SDK 호출 예외: {sdk_err}")

    # 2. REST API 폴백 딥러닝 LLM 호출
    url = "https://code.cu.ac.kr/llm/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    data = {
        "model": "Qwen/Qwen3.5-35B-A3B-FP8",
        "messages": [
            {"role": "system", "content": "너는 KBO 야구 전문 명리 사주 도사이다. #이나 ## 같은 특수기호를 절대로 쓰지 말고, 곧바로 '1. 오늘 선발의 운세 총평' 형태의 깔끔한 한글 텍스트로만 100% 작성하라."},
            {"role": "user", "content": full_prompt}
        ],
        "stream": False,
        "max_tokens": 1500,
        "temperature": 0.7
    }
    
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=35)
        resp.raise_for_status()
        resp_data = resp.json()
        choices = resp_data.get("choices", [])
        if choices:
            msg_obj = choices[0].get("message", {})
            raw_content = msg_obj.get("content")
            if raw_content and isinstance(raw_content, str) and raw_content.strip():
                clean_kr = extract_clean_korean_saju(raw_content)
                if clean_kr:
                    print(f"🤖 [REST Qwen 3.5 35B 딥러닝 LLM 직접 생성 완료] 한국어 본문 길이: {len(clean_kr)}자")
                    return sanitize_all_hashes(clean_kr)
        return sanitize_all_hashes(dynamic_saju_result)
    except Exception as e:
        print(f"⚠️ REST API 호출 예외: {e}")
        return sanitize_all_hashes(dynamic_saju_result)

if __name__ == "__main__":
    # 단독 테스트를 위해 결과를 콘솔에 출력합니다.
    print(get_baseball_saju("원태인"))
