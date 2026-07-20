"""
deep_learning/saju_sentiment.py
==============================
뉴스 기사 감성 분석 모듈 (Phase 1)

[우선순위]
  1순위: KoBERT 계열 transformers 모델 (정확도 높음)
  2순위: 키워드 기반 룰 백업 (모델 미설치 시 자동 전환)

[AWS MySQL 저장]
  - 감성 분석 결과를 news_sentiment_cache 테이블에 캐싱
  - 동일 뉴스 재분석 방지 (title 기준 중복 체크)

[출력 예시]
  {
    "positive": 0.15,
    "negative": 0.72,
    "neutral":  0.13,
    "momentum": "하락기운",       # 사주 프롬프트용
    "ohaeng_hint": "음(陰)의 기운이 짙으니 수비적 운세가 감지됨",
    "key_signals": ["체력 저하", "천적 타자", "4이닝 고전"]
  }

[필요 패키지] (선택적)
  pip install transformers torch sentencepiece
"""

import os
import re
import json
import pymysql
from datetime import datetime

# ── transformers 선택적 임포트 ─────────────────────────────────────────────────
_SENTIMENT_PIPE = None

def _load_sentiment_model():
    """감성 분석 파이프라인 로드 (최초 1회, 이후 캐싱)"""
    global _SENTIMENT_PIPE
    if _SENTIMENT_PIPE is not None:
        return _SENTIMENT_PIPE
    try:
        from transformers import pipeline
        # 한국어 뉴스/금융 특화 경량 BERT 계열
        # 대안 모델: "snunlp/KR-FinBert-SC", "klue/roberta-base"
        _SENTIMENT_PIPE = pipeline(
            "text-classification",
            model="snunlp/KR-FinBert-SC",
            tokenizer="snunlp/KR-FinBert-SC",
            device=-1,        # CPU 사용 (GPU PC에서는 device=0)
            return_all_scores=True,
        )
        print("✅ KoBERT 감성 분석 모델 로드 완료 (snunlp/KR-FinBert-SC)")
        return _SENTIMENT_PIPE
    except Exception as e:
        print(f"⚠️ transformers 모델 로드 실패 → 키워드 기반 룰로 대체: {e}")
        return None


# ── .env 로드 ─────────────────────────────────────────────────────────────────
def _load_env(filepath=".env"):
    if not os.path.exists(filepath) and filepath == ".env":
        parent_env = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(parent_env):
            filepath = parent_env
    if not os.path.exists(filepath):
        return
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

_load_env()


# ── DB 연결 헬퍼 ──────────────────────────────────────────────────────────────
def _get_conn():
    return pymysql.connect(
        host=os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASSWORD", "12341234"),
        database=os.getenv("DB_NAME", "total_db"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
    )


def _ensure_cache_table(conn):
    """감성 분석 캐시 테이블 생성 (없을 경우)"""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS news_sentiment_cache (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                title_hash   VARCHAR(64) NOT NULL UNIQUE,
                title        VARCHAR(500),
                positive     FLOAT,
                negative     FLOAT,
                neutral      FLOAT,
                momentum     VARCHAR(20),
                key_signals  JSON,
                analyzed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


# ── 키워드 기반 룰 백업 ───────────────────────────────────────────────────────
_NEGATIVE_KW = [
    "부상", "이탈", "고전", "부진", "패배", "실점", "방어율 악화", "체력 저하",
    "피로", "우려", "천적", "홈런", "난타", "교체", "강판", "불안", "저조"
]
_POSITIVE_KW = [
    "완봉", "호투", "승리", "삼진", "무실점", "무사", "퍼펙트", "살벌한 구위",
    "호성적", "연승", "최고", "기대", "컨디션 최상", "복귀", "완투"
]

def _rule_based_sentiment(texts: list[str]) -> dict:
    """transformers 없을 때 사용하는 키워드 기반 감성 추정"""
    combined = " ".join(texts)
    neg_count = sum(1 for kw in _NEGATIVE_KW if kw in combined)
    pos_count = sum(1 for kw in _POSITIVE_KW if kw in combined)
    total = max(neg_count + pos_count, 1)

    neg_ratio = round(neg_count / total, 2)
    pos_ratio = round(pos_count / total, 2)
    neu_ratio = round(1.0 - neg_ratio - pos_ratio, 2)

    # 주요 신호 추출 (최대 3개)
    signals = [kw for kw in _NEGATIVE_KW if kw in combined][:2]
    signals += [kw for kw in _POSITIVE_KW if kw in combined][:1]

    momentum = "하락기운" if neg_ratio > 0.5 else "상승기운" if pos_ratio > 0.5 else "중립기운"
    return {
        "positive": pos_ratio,
        "negative": neg_ratio,
        "neutral":  max(neu_ratio, 0),
        "momentum": momentum,
        "key_signals": signals,
        "method": "rule_based",
    }


# ── KoBERT 기반 감성 분석 ────────────────────────────────────────────────────
def _bert_sentiment(texts: list[str], pipe) -> dict:
    """transformers 파이프라인으로 각 뉴스 텍스트 분석 후 평균"""
    pos_scores, neg_scores, neu_scores = [], [], []
    label_map = {
        "positive": "positive", "pos": "positive",
        "negative": "negative", "neg": "negative",
        "neutral":  "neutral",  "neu": "neutral",
    }

    for text in texts:
        try:
            text_trunc = text[:512]
            results = pipe(text_trunc)[0]   # return_all_scores=True → 리스트 반환
            score_dict = {label_map.get(r["label"].lower(), r["label"].lower()): r["score"]
                          for r in results}
            pos_scores.append(score_dict.get("positive", 0.0))
            neg_scores.append(score_dict.get("negative", 0.0))
            neu_scores.append(score_dict.get("neutral",  0.0))
        except Exception:
            pos_scores.append(0.33)
            neg_scores.append(0.33)
            neu_scores.append(0.34)

    avg_pos = round(sum(pos_scores) / len(pos_scores), 3)
    avg_neg = round(sum(neg_scores) / len(neg_scores), 3)
    avg_neu = round(sum(neu_scores) / len(neu_scores), 3)

    momentum = "하락기운" if avg_neg > 0.5 else "상승기운" if avg_pos > 0.5 else "중립기운"
    return {
        "positive": avg_pos,
        "negative": avg_neg,
        "neutral":  avg_neu,
        "momentum": momentum,
        "method": "kobert",
    }


# ── 사주용 한국어 해석 텍스트 생성 ───────────────────────────────────────────
def _build_ohaeng_hint(result: dict) -> str:
    """감성 결과 → 오행 명리 해석 힌트 문자열"""
    momentum = result.get("momentum", "중립기운")
    neg      = result.get("negative", 0)
    pos      = result.get("positive", 0)
    signals  = result.get("key_signals", [])

    if momentum == "하락기운":
        hint = f"음(陰)의 기운이 짙으니 수비적 운세가 감지됨 (부정 신호 {neg:.0%})"
    elif momentum == "상승기운":
        hint = f"양(陽)의 기운이 충만하니 공세적 피칭 운이 열려 있음 (긍정 신호 {pos:.0%})"
    else:
        hint = f"음양(陰陽)이 균형을 이루어 안정적 피칭 흐름이 예상됨"

    if signals:
        hint += f" | 핵심 변수: {', '.join(signals[:3])}"
    return hint


# ── 공개 API ──────────────────────────────────────────────────────────────────
def analyze_news_sentiment(news_list: list[dict]) -> dict:
    """
    뉴스 리스트를 감성 분석하여 사주 프롬프트용 결과 반환.

    Parameters
    ----------
    news_list : list of {"title": str, "content_raw": str}

    Returns
    -------
    dict with keys: positive, negative, neutral, momentum, ohaeng_hint, key_signals
    """
    if not news_list:
        return {
            "positive": 0.33, "negative": 0.33, "neutral": 0.34,
            "momentum": "중립기운", "ohaeng_hint": "뉴스 데이터 없음",
            "key_signals": []
        }

    texts = [
        f"{n.get('title', '')} {n.get('content_raw', '')[:200]}"
        for n in news_list
    ]

    # 1) KoBERT 시도
    pipe = _load_sentiment_model()
    if pipe:
        result = _bert_sentiment(texts, pipe)
    else:
        result = _rule_based_sentiment(texts)

    # 2) 키 신호 추출 (룰 기반 보조)
    combined = " ".join(texts)
    key_signals = [kw for kw in _NEGATIVE_KW + _POSITIVE_KW if kw in combined][:4]
    result["key_signals"] = key_signals

    # 3) 사주용 힌트 생성
    result["ohaeng_hint"] = _build_ohaeng_hint(result)

    # 4) MySQL 캐싱 (비동기 아님, 실패해도 계속)
    try:
        conn = _get_conn()
        _ensure_cache_table(conn)
        import hashlib
        titles_raw = "|".join(n.get("title", "") for n in news_list)
        title_hash = hashlib.md5(titles_raw.encode()).hexdigest()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT IGNORE INTO news_sentiment_cache
                    (title_hash, title, positive, negative, neutral, momentum, key_signals)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                title_hash,
                titles_raw[:490],
                result["positive"],
                result["negative"],
                result["neutral"],
                result["momentum"],
                json.dumps(key_signals, ensure_ascii=False),
            ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # 캐싱 실패는 무시

    return result


# ── 단독 테스트 ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample = [
        {"title": "원태인, 무더위 속 체력 저하 우려... 지난 경기 4이닝 고전",
         "content_raw": "최근 투구 이닝이 많아 체력 소모가 다소 심한 편"},
        {"title": "감독 왈, '원태인이 어깨 무거워 보이지만 끝까지 믿는다'",
         "content_raw": "구단의 전폭적인 지지를 받으나 피로도 관리가 시급"},
        {"title": "KIA 최형우, 원태인 대상 통산 5홈런 극강 천적",
         "content_raw": "천적 타자와의 승부가 오늘 경기의 핵심 변수"},
    ]
    result = analyze_news_sentiment(sample)
    print("\n📊 감성 분석 결과:")
    for k, v in result.items():
        print(f"  {k}: {v}")
