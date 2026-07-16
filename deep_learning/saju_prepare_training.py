"""
deep_learning/saju_prepare_training.py
======================================
딥러닝 학습 데이터 준비 스크립트 (현재 랩탑에서 실행)

[실행 흐름]
  1. kbo_pitcher_stats, kbo_hitter_stats DB 데이터 로드
  2. 피처 엔지니어링 (최근 N경기 이동평균, 상대팀 OPS 등)
  3. training_features 테이블에 저장 (AWS MySQL)
  4. training_jobs 상태를 'READY'로 업데이트
  5. ⛔ 여기서 종료 — 실제 학습은 GPU PC에서 saju_train_gpu.py 실행

[GPU PC에서 할 일]
  python deep_learning/saju_train_gpu.py
"""

import os
import sys
import json
import pymysql
import numpy as np
from datetime import datetime

try:
    import pandas as pd
    PANDAS_OK = True
except ImportError:
    PANDAS_OK = False
    print("❌ pandas 미설치. pip install pandas numpy")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── .env 로드 ─────────────────────────────────────────────────────────────────
def load_env(filepath=".env"):
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

load_env()


# ── DB 연결 ───────────────────────────────────────────────────────────────────
def get_conn():
    return pymysql.connect(
        host=os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASSWORD", "12341234"),
        database="statistics_db",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


# ── training_features 테이블 생성 ────────────────────────────────────────────
def init_feature_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS training_features (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                player_name  VARCHAR(50),
                season       INT,
                -- 피처 컬럼 (피처 엔지니어링 결과)
                era          FLOAT,
                whip         FLOAT,
                so_per_9     FLOAT,   -- K/9
                bb_per_9     FLOAT,   -- BB/9
                hr_per_9     FLOAT,   -- HR/9
                k_bb_ratio   FLOAT,   -- K/BB
                era_z        FLOAT,   -- ERA z-score (시즌 평균 대비)
                -- 타자 지표 (상대팀 OPS 평균)
                opp_ops      FLOAT,
                opp_avg      FLOAT,
                -- 레이블 (예측 목표)
                label_era    FLOAT,   -- 다음 시즌 ERA (회귀)
                label_ace    INT,     -- 에이스 여부 (ERA < 3.5 → 1, 아니면 0)
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


# ── 피처 엔지니어링 ───────────────────────────────────────────────────────────
def engineer_features(conn) -> "pd.DataFrame":
    """
    kbo_pitcher_stats + kbo_hitter_stats →
    학습용 피처 데이터프레임 생성
    """
    if not PANDAS_OK:
        raise RuntimeError("pandas가 설치되지 않았습니다.")

    print("📥 투수 스탯 로드 중...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT player_name, team, season, era, whip, games,
                   wins, losses, so, bb, hr, ip
            FROM kbo_pitcher_stats
            WHERE games > 5
            ORDER BY player_name, season
        """)
        pitchers = cur.fetchall()

    print("📥 타자 스탯 로드 중...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT team, season, AVG(ops) AS avg_ops, AVG(avg) AS avg_avg
            FROM kbo_hitter_stats
            GROUP BY team, season
        """)
        hitters = cur.fetchall()

    if not pitchers:
        raise RuntimeError("kbo_pitcher_stats에 데이터가 없습니다. kbo_crawler.py를 먼저 실행하세요.")

    df = pd.DataFrame(pitchers)
    hdf = pd.DataFrame(hitters)

    # IP를 float으로 변환 (6.2 → 6.67)
    def ip_to_float(ip_str):
        try:
            parts = str(ip_str).split(".")
            inn = int(parts[0])
            frac = int(parts[1]) / 3 if len(parts) > 1 else 0
            return inn + frac
        except Exception:
            return 0.0

    df["ip_float"] = df["ip"].apply(ip_to_float)
    df["ip_float"] = df["ip_float"].replace(0, 0.1)  # 0-division 방지

    # 파생 피처 계산
    df["so_per_9"] = (df["so"]  / df["ip_float"] * 9).round(2)
    df["bb_per_9"] = (df["bb"]  / df["ip_float"] * 9).round(2)
    df["hr_per_9"] = (df["hr"]  / df["ip_float"] * 9).round(2)
    df["k_bb_ratio"] = (df["so"] / df["bb"].replace(0, 0.1)).round(2)

    # ERA z-score (시즌별 정규화)
    df["era_z"] = df.groupby("season")["era"].transform(
        lambda x: (x - x.mean()) / (x.std() + 1e-6)
    ).round(3)

    # 상대 OPS 조인
    df = df.merge(hdf, on=["team", "season"], how="left", suffixes=("", "_opp"))
    df = df.rename(columns={"avg_ops": "opp_ops", "avg_avg": "opp_avg"})

    # 레이블: 다음 시즌 ERA (회귀 타겟)
    df_sorted = df.sort_values(["player_name", "season"])
    df_sorted["label_era"]  = df_sorted.groupby("player_name")["era"].shift(-1)
    df_sorted["label_ace"]  = (df_sorted["label_era"] < 3.5).astype(int)

    # 레이블이 없는 마지막 시즌 제거 (현재 시즌 → 미래 레이블 없음)
    df_sorted = df_sorted.dropna(subset=["label_era"])

    print(f"✅ 피처 엔지니어링 완료: {len(df_sorted)}개 샘플")
    return df_sorted


# ── DB에 피처 저장 ────────────────────────────────────────────────────────────
def save_features(conn, df: "pd.DataFrame") -> int:
    saved = 0
    with conn.cursor() as cur:
        # 기존 데이터 클리어 후 재적재
        cur.execute("TRUNCATE TABLE training_features")
        for _, row in df.iterrows():
            try:
                cur.execute("""
                    INSERT INTO training_features
                        (player_name, season, era, whip, so_per_9, bb_per_9, hr_per_9,
                         k_bb_ratio, era_z, opp_ops, opp_avg, label_era, label_ace)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    row.get("player_name"),
                    int(row.get("season", 0)),
                    float(row.get("era", 0) or 0),
                    float(row.get("whip", 0) or 0),
                    float(row.get("so_per_9", 0) or 0),
                    float(row.get("bb_per_9", 0) or 0),
                    float(row.get("hr_per_9", 0) or 0),
                    float(row.get("k_bb_ratio", 0) or 0),
                    float(row.get("era_z", 0) or 0),
                    float(row.get("opp_ops", 0) or 0),
                    float(row.get("opp_avg", 0) or 0),
                    float(row.get("label_era", 0) or 0),
                    int(row.get("label_ace", 0) or 0),
                ))
                saved += 1
            except Exception as e:
                print(f"  ⚠️ 피처 저장 실패 ({row.get('player_name')}): {e}")
    conn.commit()
    return saved


# ── training_jobs READY 업데이트 ─────────────────────────────────────────────
def mark_ready(conn, n_samples: int):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE training_jobs SET status = 'READY', notes = %s, updated_at = NOW()
            WHERE status = 'COLLECTING'
            ORDER BY id DESC LIMIT 1
        """, (f"피처 {n_samples}개 준비 완료. GPU PC에서 학습 시작 가능.",))
        if cur.rowcount == 0:
            # COLLECTING 잡이 없으면 새로 삽입
            cur.execute("""
                INSERT INTO training_jobs (job_name, status, notes)
                VALUES (%s, 'READY', %s)
            """, (
                f"saju_dl_{datetime.now().strftime('%Y%m%d_%H%M')}",
                f"피처 {n_samples}개 준비 완료. GPU PC에서 python deep_learning/saju_train_gpu.py 실행하세요.",
            ))
    conn.commit()


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print("  📊 KBO 딥러닝 학습 데이터 준비 시작")
    print(f"{'='*60}\n")

    try:
        conn = get_conn()
    except Exception as e:
        print(f"❌ DB 연결 실패: {e}")
        return

    init_feature_table(conn)

    try:
        df = engineer_features(conn)
    except RuntimeError as e:
        print(f"❌ 피처 엔지니어링 실패: {e}")
        conn.close()
        return

    n = save_features(conn, df)
    mark_ready(conn, n)
    conn.close()

    print(f"\n{'='*60}")
    print(f"  ✅ 학습 데이터 준비 완료 ({n}개 샘플 → MySQL training_features)")
    print(f"")
    print(f"  ⛔ 이 PC에서의 작업은 여기서 종료합니다.")
    print(f"  GPU PC에서 아래 명령을 실행하세요:")
    print(f"")
    print(f"    git pull")
    print(f"    python deep_learning/saju_train_gpu.py")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
