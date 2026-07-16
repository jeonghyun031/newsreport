import os
import re
import glob
import pandas as pd
from sqlalchemy import create_engine, types

# 1. .env 파일 파싱
def load_env():
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

env = load_env()

# DB 연결 정보 추출
endpoint = env.get("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com")
port = env.get("DB_PORT", "3306")
user = env.get("DB_USER", "admin")
password = env.get("DB_PASSWORD", "12341234")
db_name = "statistics_db"

print("[INFO] KBO statistics database load process starting...")

# 데이터베이스 생성 시도
try:
    # 기본 접속용 mysql 시스템 DB를 매개로 연결해 CREATE DATABASE 수행
    temp_url = f"mysql+pymysql://{user}:{password}@{endpoint}:{port}/mysql"
    temp_engine = create_engine(temp_url)
    with temp_engine.connect() as conn:
        # DDL 명령은 auto-commit 하도록 실행
        conn.execute("SET autocommit = 1")
        conn.execute(f"CREATE DATABASE IF NOT EXISTS {db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    print(f"[OK] Database '{db_name}' verified/created successfully!")
except Exception as e:
    print(f"[WARN] Database creation failed (User might need to create it manually): {e}")

# 정식 statistics_db 접속 엔진 생성
db_url = f"mysql+pymysql://{user}:{password}@{endpoint}:{port}/{db_name}"
engine = create_engine(db_url)

crawling_dir = "Crawling"

# 2. 공격 데이터(Attack Record) 로드 및 가공
print("\n[INFO] 1. Processing KBO Attack data...")
attack_files = glob.glob(os.path.join(crawling_dir, "KBO_Attack_Record(*).csv"))
attack_dfs = []

attack_cols_map = {
    "순위": "ranking", "팀": "team", "타수": "ab", "안타": "hits", 
    "2루타": "b2", "3루타": "b3", "홈런": "hr", "타점": "rbi", 
    "득점": "runs", "도루": "sb", "사사구": "bb_hp", "삼진": "so", 
    "병살": "gdp", "타율": "avg", "출루율": "obp", "장타율": "slg", "OPS": "ops"
}

for filepath in attack_files:
    # 파일명에서 연도 추출 (예: KBO_Attack_Record(2026).csv -> 2026)
    match = re.search(r'Record\((\d{4})\)', os.path.basename(filepath))
    if not match:
        continue
    year = int(match.group(1))
    
    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df = df.rename(columns=attack_cols_map)
    df["year"] = year
    attack_dfs.append(df)
    print(f"[LOAD] Attack CSV loaded ({year} year, {len(df)} teams)")

if attack_dfs:
    final_attack_df = pd.concat(attack_dfs, ignore_index=True)
    
    # DB 적재 스키마 정의
    sql_types = {
        "ranking": types.INTEGER(),
        "team": types.VARCHAR(50),
        "ab": types.INTEGER(),
        "hits": types.INTEGER(),
        "b2": types.INTEGER(),
        "b3": types.INTEGER(),
        "hr": types.INTEGER(),
        "rbi": types.INTEGER(),
        "runs": types.INTEGER(),
        "sb": types.INTEGER(),
        "bb_hp": types.INTEGER(),
        "so": types.INTEGER(),
        "gdp": types.INTEGER(),
        "avg": types.FLOAT(),
        "obp": types.FLOAT(),
        "slg": types.FLOAT(),
        "ops": types.FLOAT(),
        "year": types.INTEGER()
    }
    
    try:
        final_attack_df.to_sql(
            name="kbo_attack",
            con=engine,
            if_exists="replace",
            index=False,
            dtype=sql_types
        )
        print("[SUCCESS] 'kbo_attack' table loaded successfully!")
    except Exception as err:
        print(f"[ERROR] 'kbo_attack' table load failed: {err}")
else:
    print("[WARN] No attack data to load.")

# 3. 수비 데이터(Defense Record) 로드 및 가공
print("\n[INFO] 2. Processing KBO Defense data...")
defense_files = glob.glob(os.path.join(crawling_dir, "KBO_Defense_Record(*).csv"))
defense_dfs = []

defense_cols_map = {
    "순위": "ranking", "팀": "team", "이닝": "innings", "피안타": "hits_allowed", 
    "피홈런": "hr_allowed", "실점": "runs_allowed", "자책": "er", 
    "사사구": "bb_allowed", "탈삼진": "so", "평균자책": "era", 
    "실책": "errors", "WHIP": "whip", "QS": "qs", "홀드": "holds", "세이브": "saves"
}

for filepath in defense_files:
    match = re.search(r'Record\((\d{4})\)', os.path.basename(filepath))
    if not match:
        continue
    year = int(match.group(1))
    
    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df = df.rename(columns=defense_cols_map)
    df["year"] = year
    defense_dfs.append(df)
    print(f"[LOAD] Defense CSV loaded ({year} year, {len(df)} teams)")

if defense_dfs:
    final_defense_df = pd.concat(defense_dfs, ignore_index=True)
    
    sql_types = {
        "ranking": types.INTEGER(),
        "team": types.VARCHAR(50),
        "innings": types.VARCHAR(50), # 이닝 분수 표기가 들어있으므로 문자열 지정
        "hits_allowed": types.INTEGER(),
        "hr_allowed": types.INTEGER(),
        "runs_allowed": types.INTEGER(),
        "er": types.INTEGER(),
        "bb_allowed": types.INTEGER(),
        "so": types.INTEGER(),
        "era": types.FLOAT(),
        "errors": types.INTEGER(),
        "whip": types.FLOAT(),
        "qs": types.INTEGER(),
        "holds": types.INTEGER(),
        "saves": types.INTEGER(),
        "year": types.INTEGER()
    }
    
    try:
        final_defense_df.to_sql(
            name="kbo_defense",
            con=engine,
            if_exists="replace",
            index=False,
            dtype=sql_types
        )
        print("[SUCCESS] 'kbo_defense' table loaded successfully!")
    except Exception as err:
        print(f"[ERROR] 'kbo_defense' table load failed: {err}")
else:
    print("[WARN] No defense data to load.")

# 4. 일정 데이터(Schedule) 로드 및 가공
print("\n[INFO] 3. Processing KBO Schedule data...")
schedule_files = glob.glob(os.path.join(crawling_dir, "KBO_Schedule(*).csv"))
schedule_dfs = []

for filepath in schedule_files:
    match = re.search(r'Schedule\((\d{6})\)', os.path.basename(filepath))
    if not match:
        continue
    year_month = match.group(1)
    
    # 헤더가 없는 CSV이므로 열 수에 따라 동적으로 names 지정
    temp_df = pd.read_csv(filepath, header=None, nrows=1, encoding="utf-8-sig")
    num_cols = temp_df.shape[1]
    if num_cols == 8:
        cols = ['date', 'time', 'away_team', 'home_team', 'stadium', 'status', 'away_pitcher', 'home_pitcher']
    else:
        cols = ['date', 'time', 'away_team', 'home_team', 'stadium', 'status']
        
    df = pd.read_csv(filepath, header=None, names=cols, encoding="utf-8-sig")
    
    # 8열이 아닌 경우 결측치로 채워줌
    if 'away_pitcher' not in df.columns:
        df['away_pitcher'] = None
    if 'home_pitcher' not in df.columns:
        df['home_pitcher'] = None
        
    df["year_month"] = year_month
    schedule_dfs.append(df)
    print(f"[LOAD] Schedule CSV loaded ({year_month} month, {len(df)} games)")

if schedule_dfs:
    final_schedule_df = pd.concat(schedule_dfs, ignore_index=True)
    
    # 날짜 데이터의 강제 문자열 변환
    final_schedule_df["date"] = final_schedule_df["date"].astype(str)
    
    sql_types = {
        "date": types.VARCHAR(20),
        "time": types.VARCHAR(20),
        "away_team": types.VARCHAR(50),
        "home_team": types.VARCHAR(50),
        "stadium": types.VARCHAR(100),
        "status": types.VARCHAR(200),
        "away_pitcher": types.VARCHAR(50),
        "home_pitcher": types.VARCHAR(50),
        "year_month": types.VARCHAR(20)
    }
    
    try:
        final_schedule_df.to_sql(
            name="kbo_schedule",
            con=engine,
            if_exists="replace",
            index=False,
            dtype=sql_types
        )
        print("[SUCCESS] 'kbo_schedule' table loaded successfully!")
    except Exception as err:
        print(f"[ERROR] 'kbo_schedule' table load failed: {err}")
else:
    print("[WARN] No schedule data to load.")

print("\n[FINISH] All KBO statistics data loading process completed.")
