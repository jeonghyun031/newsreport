"""
deep_learning/saju_rag.py
=========================
명리학 지식 RAG (Retrieval-Augmented Generation) 모듈 (Phase 3)

[아키텍처]
  - 벡터 저장소: AWS MySQL (saju_knowledge 테이블, embedding JSON 컬럼)
  - 임베딩 모델: paraphrase-multilingual-MiniLM-L12-v2 (384차원, 한국어 지원)
  - 모델 미설치 시: MySQL FULLTEXT 검색으로 자동 백업

[주요 기능]
  1. init_knowledge_base()   → 명리학 지식 청크를 MySQL에 임베딩 저장 (최초 1회)
  2. get_saju_pillar(birthday) → 생년월일 → 연간·월간·일간 천간지지 계산
  3. query_rag(query, top_k)  → 관련 명리학 지식 검색 → LLM 프롬프트용 텍스트 반환

[저장 DB]
  total_db.saju_knowledge (embedding JSON, ~384 float per row)
"""

import os
import json
import math
import hashlib
import pymysql
from datetime import datetime, date
from typing import Optional

# ── 선택적 임포트 ─────────────────────────────────────────────────────────────
_EMBED_MODEL = None

def _load_embed_model():
    global _EMBED_MODEL
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _EMBED_MODEL = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        print("✅ 임베딩 모델 로드 완료 (paraphrase-multilingual-MiniLM-L12-v2)")
        return _EMBED_MODEL
    except Exception as e:
        print(f"⚠️ sentence-transformers 미설치 → FULLTEXT 백업 모드 전환: {e}")
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


# ── DB 연결 ───────────────────────────────────────────────────────────────────
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


# ── 명리학 지식베이스 ─────────────────────────────────────────────────────────
# 각 청크: {"category": str, "content": str}
SAJU_KNOWLEDGE_BASE = [
    # ── 오행 기본 ──────────────────────────────────────────────────────────────
    {
        "category": "오행_기본",
        "content": "오행(五行)은 목(木)·화(火)·토(土)·금(金)·수(水)의 다섯 가지 기운이다. "
                   "목(木)은 성장·확산·봄을 상징하고, 화(火)는 열정·맹렬함·여름을, "
                   "토(土)는 중용·안정·환절기를, 금(金)은 날카로움·수확·가을을, "
                   "수(水)는 지혜·저장·겨울을 상징한다.",
    },
    {
        "category": "오행_상생",
        "content": "오행 상생(相生): 목생화(木生火) - 나무가 불을 키우고, "
                   "화생토(火生土) - 불이 흙을 만들고, 토생금(土生金) - 흙이 쇠를 품으며, "
                   "금생수(金生水) - 쇠가 물을 생하고, 수생목(水生木) - 물이 나무를 기른다. "
                   "상생 관계는 서로 돕고 강화시키는 순환 에너지다.",
    },
    {
        "category": "오행_상극",
        "content": "오행 상극(相剋): 목극토(木剋土) - 나무가 흙을 파고들고, "
                   "화극금(火剋金) - 불이 쇠를 녹이며, 토극수(土剋水) - 흙이 물을 막고, "
                   "금극목(金剋木) - 쇠가 나무를 자르며, 수극화(水剋火) - 물이 불을 끈다. "
                   "상극 관계는 억누르고 제어하는 긴장 에너지다.",
    },
    # ── 천간 10개 ──────────────────────────────────────────────────────────────
    {
        "category": "천간",
        "content": "천간(天干) 10개: 甲(갑·목·양), 乙(을·목·음), 丙(병·화·양), 丁(정·화·음), "
                   "戊(무·토·양), 己(기·토·음), 庚(경·금·양), 辛(신·금·음), "
                   "壬(임·수·양), 癸(계·수·음). "
                   "갑목은 큰 나무처럼 당당하고 추진력이 있으며, 병화는 태양처럼 밝고 활동적이다.",
    },
    {
        "category": "천간_갑목",
        "content": "甲木(갑목) 일간: 곧고 당당한 큰 나무. 리더십이 강하고 개척 정신이 뛰어나다. "
                   "야구에 비유하면 팀의 에이스처럼 굳건히 서서 팀을 이끄는 타입. "
                   "화(火)가 강하면 에너지 소진 주의, 금(金)이 강하면 제어력 발동.",
    },
    {
        "category": "천간_병화",
        "content": "丙火(병화) 일간: 태양처럼 뜨겁고 밝은 에너지. 외향적이고 열정적이며 순발력이 뛰어나다. "
                   "마운드 위에서 화(火) 기운이 충만할 때 구위가 폭발하나, "
                   "수(水)의 기운이 부족하면 제구력(金氣) 조절에 어려움이 생길 수 있다.",
    },
    {
        "category": "천간_경금",
        "content": "庚金(경금) 일간: 단단한 쇠붙이. 원칙적이고 결단력이 있으며 냉철하다. "
                   "KBO 제구력(Location)이 높은 투수에게서 자주 나타나는 기질. "
                   "목(木)이 강한 상대 타자와의 상극 관계에서 힘겨운 승부가 펼쳐진다.",
    },
    # ── 지지 12개 ──────────────────────────────────────────────────────────────
    {
        "category": "지지",
        "content": "지지(地支) 12개: 子(자·수), 丑(축·토), 寅(인·목), 卯(묘·목), "
                   "辰(진·토), 巳(사·화), 午(오·화), 未(미·토), "
                   "申(신·금), 酉(유·금), 戌(술·토), 亥(해·수). "
                   "자(子)는 쥐로 지혜와 민첩함, 오(午)는 말로 강렬한 화(火) 에너지를 상징한다.",
    },
    # ── 계절(월령)별 득령 판단 ─────────────────────────────────────────────────
    {
        "category": "계절_득령",
        "content": "득령(得令): 자신의 오행과 같은 계절에 태어나면 기운이 강해진다. "
                   "봄(寅卯月)은 목(木) 득령, 여름(巳午月)은 화(火) 득령, "
                   "가을(申酉月)은 금(金) 득령, 겨울(亥子月)은 수(水) 득령. "
                   "실령(失令)은 반대 계절에 태어나 기운이 약해진 상태를 의미한다.",
    },
    {
        "category": "계절_여름",
        "content": "여름(5~7월) 경기: 화(火) 기운이 극성하는 계절. "
                   "화(火) 일간 투수(丙·丁)는 에너지가 넘치나 체력 소진 주의. "
                   "수(水) 일간 투수(壬·癸)는 화극수(火剋水)의 압박을 받아 멘탈 조율이 필요하다. "
                   "금(金) 일간 투수(庚·辛)는 화극금(火剋金)으로 제구력 불안정 가능성.",
    },
    {
        "category": "계절_봄",
        "content": "봄(3~4월) 경기: 목(木) 기운이 왕성한 계절. "
                   "목(木) 일간 투수(甲·乙)는 득령하여 에너지 충만. "
                   "토(土) 일간 투수(戊·己)는 목극토(木剋土)의 상극을 받아 위기 관리에 집중 요망. "
                   "개막 초 컨디션 점검 단계로 안정적 피칭 운이 선행되어야 한다.",
    },
    {
        "category": "계절_가을",
        "content": "가을(9~10월) 경기: 금(金) 기운이 강한 수확의 계절. "
                   "금(金) 일간 투수(庚·辛)는 제구력(Location)이 최고조에 달한다. "
                   "목(木) 일간 투수(甲·乙)는 금극목(金剋木)으로 부상 위험 증가. "
                   "가을야구(포스트시즌)는 토(土)의 중용과 인내가 더욱 중요해지는 시기.",
    },
    # ── 사주와 KBO 스탯 연결 ──────────────────────────────────────────────────
    {
        "category": "야구_오행_매핑",
        "content": "KBO Talent 스탯과 오행 매핑: "
                   "K-Stuff+(순수 구위)는 화(火)의 기운 - 수치가 높을수록 마운드를 압도하는 불꽃 에너지. "
                   "K-Location+(제구력)는 금(金)의 기운 - 칼날 같은 쇠붙이의 정교함. "
                   "FCB.OSWC(위기 담력)는 토(土)의 기운 - 풍파에 흔들리지 않는 태산의 우직함. "
                   "세 오행의 조화가 에이스 투수의 조건이다.",
    },
    {
        "category": "야구_천적_상극",
        "content": "천적 타자와의 상극 분석: "
                   "투수의 일간 오행과 타자의 오행이 상극 관계일 때 천적이 된다. "
                   "예) 甲木(갑목) 투수 vs 庚金(경금) 타자 → 금극목(金剋木)으로 타자 유리. "
                   "상극을 극복하려면 투수는 득령(得令)하거나 용신(用神)이 강해야 한다. "
                   "천적 타자 등판 시 볼 배합 변화와 전략적 투구가 액막이가 된다.",
    },
    {
        "category": "야구_에이스_기질",
        "content": "에이스 투수의 명리학적 기질: "
                   "일간이 양간(甲·丙·戊·庚·壬)이면 주도적·공격적 피칭 성향. "
                   "일간이 음간(乙·丁·己·辛·癸)이면 세밀하고 변화구 중심의 기교파 성향. "
                   "화(火) 기운이 강한 투수는 빠른 볼 의존도가 높고 체력 소진이 빠르다. "
                   "금(金) 기운이 강한 투수는 제구와 변화구로 타자를 요리하는 타입이다.",
    },
    {
        "category": "야구_위기_토기",
        "content": "위기 상황(OSWC)과 토(土)의 기운: "
                   "토(土)는 중화·중용의 기운으로 혼란한 상황에서도 중심을 잡는 힘이다. "
                   "OSWC(위기 상황 담력)가 높은 투수는 토(土)의 기운이 충만한 것. "
                   "만루 위기에서 토(土) 기운이 약하면 흔들리고, "
                   "강하면 '위기를 즐기는' 에이스의 풍모를 발휘한다.",
    },
    # ── 오늘의 일진 관련 ──────────────────────────────────────────────────────
    {
        "category": "일진_갑일",
        "content": "갑(甲)일: 시작과 개척의 에너지가 강한 날. "
                   "새로운 것을 시도하기 좋고, 선발 투수에게 공격적 피칭을 권한다. "
                   "갑목 일간 투수에게는 같은 기운의 날이므로 컨디션 최고조일 가능성.",
    },
    {
        "category": "일진_경일",
        "content": "경(庚)일: 결단·수확의 에너지가 강한 날. "
                   "정교한 제구와 냉철한 판단력이 빛을 발하는 날. "
                   "금(金) 일간 투수(庚·辛)에게 최적의 컨디션이 기대되며, "
                   "타자들은 경일에 금기운이 강해 삼진을 많이 당하는 경향이 있다.",
    },
    {
        "category": "일진_병일",
        "content": "병(丙)일: 태양의 에너지가 넘치는 날. 구위가 최고조이며 공격적 투구 기회. "
                   "단, 화(火)의 과잉으로 제구력이 흔들릴 수 있어 볼배합 조율이 관건. "
                   "수(水) 기운의 타자들에게는 병일 화기운이 위협적으로 작용.",
    },
    # ── 용신과 희신 ───────────────────────────────────────────────────────────
    {
        "category": "용신_개념",
        "content": "용신(用神): 사주에서 균형을 잡아주는 가장 필요한 오행. "
                   "화(火) 과다 사주에서는 수(水)가 용신, 목(木) 과다에서는 금(金)이 용신. "
                   "KBO 투수에서 구위(火)가 극강하나 제구(金)가 약하면 금(金) 용신이 필요하고, "
                   "반대로 제구는 좋지만 볼끝이 약하면 화(火) 용신을 키워야 한다.",
    },
    {
        "category": "희신_기신",
        "content": "희신(喜神): 용신을 도와주는 기운. 기신(忌神): 용신을 해치는 기운. "
                   "오늘 경기에서 기신의 영향이 크면 액운이 끼어 있다고 본다. "
                   "뉴스의 부정적 신호(체력 저하, 천적 타자 등)는 기신의 발동으로 해석한다.",
    },
]


# ── 천간지지 사주 계산 ────────────────────────────────────────────────────────
HEAVENLY_STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
STEMS_KR       = ["갑", "을", "병", "정", "무", "기", "경", "신", "임", "계"]
EARTHLY_BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
BRANCHES_KR      = ["자", "축", "인", "묘", "진", "사", "오", "미", "신", "유", "술", "해"]

STEM_ELEMENT = {
    "甲": "목(木)", "乙": "목(木)", "丙": "화(火)", "丁": "화(火)",
    "戊": "토(土)", "己": "토(土)", "庚": "금(金)", "辛": "금(金)",
    "壬": "수(水)", "癸": "수(水)",
}
BRANCH_ELEMENT = {
    "子": "수(水)", "丑": "토(土)", "寅": "목(木)", "卯": "목(木)",
    "辰": "토(土)", "巳": "화(火)", "午": "화(火)", "未": "토(土)",
    "申": "금(金)", "酉": "금(金)", "戌": "토(土)", "亥": "수(水)",
}


def _parse_birthday(birthday_str: str) -> Optional[tuple[int, int, int]]:
    """
    '2000년 4월 6일' 또는 '2000-04-06' 형식 → (year, month, day) 반환
    """
    import re
    patterns = [
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        r"(\d{4})\.(\d{1,2})\.(\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, birthday_str)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def get_saju_pillar(birthday_str: str) -> dict:
    """
    생년월일 문자열 → 연간·월간·일간 천간지지 계산 반환

    Returns
    -------
    dict: {
        "year_stem": "甲", "year_branch": "子",
        "month_stem": "丙", "month_branch": "寅",
        "day_stem": "庚", "day_branch": "戌",
        "year_element": "목(木)", "day_element": "금(金)",
        "season": "봄", "summary": "갑자년 병인월 경술일 ..."
    }
    """
    parsed = _parse_birthday(birthday_str)
    if not parsed:
        return {"summary": "생년월일 파싱 불가", "year_element": "토(土)", "day_element": "토(土)"}

    year, month, day = parsed

    # ── 연간·연지 계산 ─────────────────────────────────────────────────────────
    year_stem_idx   = (year - 4) % 10
    year_branch_idx = year % 12
    year_stem   = HEAVENLY_STEMS[year_stem_idx]
    year_branch = EARTHLY_BRANCHES[year_branch_idx]

    # ── 월간·월지 계산 (간략화: 절기 무시, 태어난 달 기준) ─────────────────────
    # 연간 기준 월간 시작 인덱스
    month_stem_start = {0: 2, 1: 4, 2: 6, 3: 8, 4: 0, 5: 2, 6: 4, 7: 6, 8: 8, 9: 0}
    base = month_stem_start.get(year_stem_idx % 5 * 2 % 10, 2)
    month_stem_idx   = (base + (month - 1)) % 10
    month_branch_idx = (month + 1) % 12   # 寅月(index 2)이 1월
    month_stem   = HEAVENLY_STEMS[month_stem_idx]
    month_branch = EARTHLY_BRANCHES[month_branch_idx]

    # ── 일간·일지 계산 (율리우스일수 기반) ────────────────────────────────────
    # 기준: 2000-01-01 = 甲戌日 (60갑자 index 10)
    try:
        ref = date(2000, 1, 1)
        target = date(year, month, day)
        delta = (target - ref).days
        day_idx  = (10 + delta) % 60
        day_stem_idx   = day_idx % 10
        day_branch_idx = day_idx % 12
    except Exception:
        day_stem_idx, day_branch_idx = 0, 0

    day_stem   = HEAVENLY_STEMS[day_stem_idx]
    day_branch = EARTHLY_BRANCHES[day_branch_idx]

    # ── 계절 ─────────────────────────────────────────────────────────────────
    season_map = {
        1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
        6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을",
        11: "가을", 12: "겨울"
    }
    season = season_map.get(month, "중립")

    summary = (
        f"{STEMS_KR[year_stem_idx]}{BRANCHES_KR[year_branch_idx]}년 "
        f"{STEMS_KR[month_stem_idx]}{BRANCHES_KR[month_branch_idx]}월 "
        f"{STEMS_KR[day_stem_idx]}{BRANCHES_KR[day_branch_idx]}일생 | "
        f"일간: {day_stem}({STEM_ELEMENT.get(day_stem, '?')}) | "
        f"출생계절: {season}"
    )

    return {
        "year_stem": year_stem, "year_branch": year_branch,
        "month_stem": month_stem, "month_branch": month_branch,
        "day_stem": day_stem, "day_branch": day_branch,
        "year_element": STEM_ELEMENT.get(year_stem, "토(土)"),
        "day_element":  STEM_ELEMENT.get(day_stem,  "토(土)"),
        "season": season,
        "summary": summary,
    }


# ── 코사인 유사도 계산 ────────────────────────────────────────────────────────
def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


# ── MySQL 테이블 초기화 ───────────────────────────────────────────────────────
def _ensure_knowledge_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS saju_knowledge (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                chunk_hash VARCHAR(32) NOT NULL UNIQUE,
                category   VARCHAR(50),
                content    TEXT,
                embedding  JSON,         -- 384차원 float 배열
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def _is_knowledge_base_empty(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM saju_knowledge")
        return cur.fetchone()["cnt"] == 0


# ── 지식베이스 초기화 (최초 1회) ─────────────────────────────────────────────
def init_knowledge_base(force: bool = False):
    """
    MySQL에 명리학 지식 청크를 임베딩하여 저장합니다.
    이미 데이터가 있으면 스킵 (force=True 시 재구축).
    """
    try:
        conn = _get_conn()
        _ensure_knowledge_table(conn)

        if not force and not _is_knowledge_base_empty(conn):
            print("ℹ️ 사주 지식베이스가 이미 MySQL에 존재합니다. 스킵.")
            conn.close()
            return

        model = _load_embed_model()
        texts  = [chunk["content"] for chunk in SAJU_KNOWLEDGE_BASE]

        if model:
            print(f"📖 명리학 지식 {len(texts)}개 청크 임베딩 생성 중...")
            embeddings = model.encode(texts, show_progress_bar=True).tolist()
        else:
            embeddings = [None] * len(texts)

        with conn.cursor() as cur:
            if force:
                cur.execute("TRUNCATE TABLE saju_knowledge")

            for chunk, emb in zip(SAJU_KNOWLEDGE_BASE, embeddings):
                chunk_hash = hashlib.md5(chunk["content"].encode()).hexdigest()
                cur.execute("""
                    INSERT IGNORE INTO saju_knowledge (chunk_hash, category, content, embedding)
                    VALUES (%s, %s, %s, %s)
                """, (
                    chunk_hash,
                    chunk["category"],
                    chunk["content"],
                    json.dumps(emb) if emb else None,
                ))
        conn.commit()
        conn.close()
        print(f"✅ MySQL 사주 지식베이스 초기화 완료! ({len(texts)}개 청크)")

    except Exception as e:
        print(f"❌ 지식베이스 초기화 실패: {e}")


# ── RAG 쿼리 ─────────────────────────────────────────────────────────────────
def query_rag(
    pitcher_name: str,
    saju_pillar: dict,
    stats: dict,
    top_k: int = 4,
) -> str:
    """
    투수 정보 + 사주 기둥 → 관련 명리학 지식 검색 → LLM 프롬프트용 텍스트 반환

    Returns
    -------
    str: 검색된 명리학 컨텍스트 (LLM 프롬프트에 바로 주입)
    """
    # 쿼리 문자열 조합
    day_elem    = saju_pillar.get("day_element", "토(土)")
    season      = saju_pillar.get("season", "봄")
    stuff_plus  = stats.get("stuff", 100)
    loc_plus    = stats.get("location", 100)

    query_str = (
        f"{pitcher_name} {day_elem} 일간 투수 "
        f"{season} 계절 "
        f"구위({'강' if stuff_plus > 110 else '약'}) "
        f"제구력({'강' if loc_plus > 110 else '약'}) "
        f"상극 천적 위기 오행"
    )

    try:
        conn = _get_conn()
        _ensure_knowledge_table(conn)

        model = _load_embed_model()

        if model:
            # ── 벡터 유사도 검색 ──────────────────────────────────────────────
            query_emb = model.encode([query_str])[0].tolist()
            with conn.cursor() as cur:
                cur.execute("SELECT category, content, embedding FROM saju_knowledge WHERE embedding IS NOT NULL")
                rows = cur.fetchall()

            scored = []
            for row in rows:
                emb = json.loads(row["embedding"])
                sim = _cosine_similarity(query_emb, emb)
                scored.append((sim, row["category"], row["content"]))
            scored.sort(key=lambda x: x[0], reverse=True)
            top_chunks = scored[:top_k]
        else:
            # ── FULLTEXT 키워드 검색 백업 ─────────────────────────────────────
            keywords = [day_elem.replace("(", "").replace(")", ""), season, "오행", "상극", "에이스"]
            results = []
            with conn.cursor() as cur:
                for kw in keywords[:3]:
                    cur.execute(
                        "SELECT category, content FROM saju_knowledge WHERE content LIKE %s LIMIT 2",
                        (f"%{kw}%",)
                    )
                    for row in cur.fetchall():
                        results.append((0.5, row["category"], row["content"]))
            top_chunks = results[:top_k]

        conn.close()

        if not top_chunks:
            return ""

        # ── 프롬프트용 텍스트 조합 ────────────────────────────────────────────
        context_lines = ["[🔯 RAG 명리학 지식베이스 - 참고 문서]"]
        for i, (sim, cat, content) in enumerate(top_chunks, 1):
            context_lines.append(f"\n[{i}. {cat}]\n{content}")
        context_lines.append(f"\n[사주 기둥 요약]\n{saju_pillar.get('summary', '')}")

        return "\n".join(context_lines)

    except Exception as e:
        print(f"⚠️ RAG 쿼리 실패: {e}")
        return f"[명리학 기본 원칙] 오행 상생상극, 일간 {day_elem} 기질 분석, {season} 계절 득령 판단"


# ── 단독 테스트 ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== 사주 기둥 계산 테스트 ===")
    pillar = get_saju_pillar("2000년 4월 6일")
    for k, v in pillar.items():
        print(f"  {k}: {v}")

    print("\n=== 지식베이스 초기화 ===")
    init_knowledge_base()

    print("\n=== RAG 쿼리 테스트 ===")
    result = query_rag(
        "원태인",
        pillar,
        {"stuff": 115, "location": 82, "crisis_mgmt": 95},
        top_k=3,
    )
    print(result)
