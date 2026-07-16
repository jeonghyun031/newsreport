import os
import sys
import glob
import pandas as pd
from sqlalchemy import create_engine, types
from datetime import datetime

# Windows 콘솔 인코딩 에러 방지 (utf-8 강제 지정)
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

print("🚀 로컬 뉴스 데이터 적재 프로세스를 시작합니다...")

# 1. .env 설정 파일 로드
env_dict = {}
env_path = ".env"
if os.path.exists(env_path):
    print("📝 .env 설정 파일을 로드합니다...")
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_dict[k.strip()] = v.strip()
else:
    print("⚠️ .env 파일을 찾을 수 없습니다. 기본값을 사용합니다.")

aws_rds_endpoint = env_dict.get("AWS_RDS_ENDPOINT", "your-aws-rds-endpoint.amazonaws.com")
db_port = env_dict.get("DB_PORT", "3306")
db_user = env_dict.get("DB_USER", "admin")
db_password = env_dict.get("DB_PASSWORD", "your-password")
db_name = env_dict.get("DB_NAME", "articel_db")

# 1.1 AWS RDS MySQL 서버에 접속하여 데이터베이스 자동 생성 시도
import pymysql
try:
    print(f"⚙️ AWS RDS MySQL 서버에 접속하여 '{db_name}' 데이터베이스 존재 여부를 확인합니다...")
    conn = pymysql.connect(
        host=aws_rds_endpoint,
        port=int(db_port),
        user=db_user,
        password=db_password,
        charset='utf8mb4'
    )
    with conn.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn.commit()
    conn.close()
    print(f"✅ 데이터베이스 '{db_name}' 준비 완료!")
except Exception as e:
    print(f"⚠️ 데이터베이스 생성/확인 실패 (권한 부족 또는 접속 불량): {e}")

# SQLAlchemy 연결 엔진 생성
db_url = f"mysql+pymysql://{db_user}:{db_password}@{aws_rds_endpoint}:{db_port}/{db_name}?charset=utf8mb4"
try:
    engine = create_engine(db_url)
except Exception as e:
    print(f"❌ DB 연결 엔진 생성 실패: {e}")
    exit(1)

# 2. 뉴스 CSV 파일 일괄 추출 (Extract)
csv_files = glob.glob(os.path.join("Crawling", "NewsList*daum.csv"))
if not csv_files:
    print("❌ Crawling 폴더에서 NewsList*daum.csv 패턴의 파일을 찾을 수 없습니다.")
    exit(1)

print(f"📡 수집 대상 CSV 파일 목록: {csv_files}")

df_list = []
for file in csv_files:
    try:
        # 파일의 컬럼 개수 자동 감지
        test_df = pd.read_csv(file, nrows=1, header=None, encoding="utf-8")
        cols_count = len(test_df.columns)
        
        if cols_count >= 5:
            col_names = ["date_str", "title", "press", "content", "url"]
        else:
            col_names = ["date_str", "title", "press", "content"]
            
        temp_df = pd.read_csv(
            file,
            header=None,
            names=col_names,
            encoding="utf-8"
        )
        
        # 4개 컬럼인 경우 빈 url 필드 생성
        if cols_count < 5:
            temp_df["url"] = ""
            
        df_list.append(temp_df)
        print(f"📥 파일 로드 완료 ({len(temp_df)}행, 컬럼수: {cols_count}개): {os.path.basename(file)}")
    except Exception as e:
        print(f"⚠️ 파일 로드 실패: {file} (에러: {e})")

if not df_list:
    print("❌ 로드된 데이터가 없습니다.")
    exit(1)

raw_df = pd.concat(df_list, ignore_index=True)
print(f"📊 총 원본 데이터 수: {len(raw_df)}개")

# 3. 데이터 정제 및 카테고리 분류 (Transform)
print("🧹 데이터 정제 및 스포츠 카테고리 분류 중...")

# Null 제거
raw_df = raw_df.dropna(subset=["date_str", "title"])

# 공백 처리
raw_df["title"] = raw_df["title"].astype(str).str.strip()
raw_df["content"] = raw_df["content"].fillna("").astype(str).str.strip()
raw_df["press"] = raw_df["press"].fillna("미상").astype(str).str.strip()
raw_df["url"] = raw_df["url"].fillna("").astype(str).str.strip()

# 날짜 포맷 변환 (2026.07.13 16:57 -> Datetime)
def parse_date(date_val):
    try:
        return pd.to_datetime(date_val, format="%Y.%m.%d %H:%M")
    except Exception:
        return pd.NaT

raw_df["date"] = raw_df["date_str"].apply(parse_date)
# 변환 실패 데이터는 드랍
raw_df = raw_df.dropna(subset=["date"])

# 카테고리 태깅 함수
def classify_category(row):
    title = str(row["title"]).lower()
    content = str(row["content"]).lower()
    combined = title + " " + content
    
    # 카테고리별 키워드
    kbo_keywords = ["kbo", "야구", "한화", "기아", "kia", "두산", "삼성", "롯데", "쓱", "ssg", "키움", "케이티", "kt", "엔씨", "nc", "엘지", "lg", "프로야구"]
    mlb_keywords = ["mlb", "메이저리그", "다저스", "양키스", "샌디에이고", "샌프란시스코", "에인절스", "토론토", "오타니", "이정후", "김하성"]
    epl_keywords = ["epl", "프리미어리그", "토트넘", "맨유", "맨시티", "아스날", "리버풀", "첼시", "손흥민", "황희찬"]
    kleague_keywords = ["k리그", "k-league", "울산", "전북", "포항", "수원", "fc서울", "광주fc", "강원fc"]
    
    if any(kw in combined for kw in kbo_keywords):
        return "KBO"
    elif any(kw in combined for kw in mlb_keywords):
        return "MLB"
    elif any(kw in combined for kw in epl_keywords):
        return "EPL"
    elif any(kw in combined for kw in kleague_keywords):
        return "K-League"
    else:
        return "General"

raw_df["category"] = raw_df.apply(classify_category, axis=1)

# 중복 제거 (제목과 본문 기준)
clean_df = raw_df.drop_duplicates(subset=["title", "content"])
# 필요한 컬럼만 선택 (url 추가)
clean_df = clean_df[["date", "title", "press", "content", "category", "url"]]

print(f"✨ 정제 후 중복 제거된 기사 수: {len(clean_df)}개")

# 4. AWS MySQL에 테이블 생성 및 데이터 적재 (Load)
print(f"🔄 AWS MySQL ({db_name}.news_articles) 테이블 생성 및 데이터 적재 중...")

# 본문 내용 유실 방지를 위해 TEXT 타입 명시 및 URL 공간 할당
sql_types = {
    "date": types.DateTime(),
    "title": types.VARCHAR(500),
    "press": types.VARCHAR(200),
    "content": types.Text(), # MySQL의 TEXT 타입 (대량의 문자열 수용 가능)
    "category": types.VARCHAR(50),
    "url": types.VARCHAR(1000)
}

try:
    # overwrite(if_exists='replace')를 통해 테이블 자동생성 및 데이터 적재
    clean_df.to_sql(
        name="news_articles",
        con=engine,
        if_exists="replace",
        index=False,
        dtype=sql_types
    )
    print("🎉 AWS MySQL에 'news_articles' 테이블 생성 및 데이터 저장 성공!")
    
    # 5. 검증: 적재된 데이터 분포 통계 출력
    print("\n🔍 [검증] AWS MySQL 데이터 분포 조회 (news_articles)")
    count_df = pd.read_sql("SELECT category, COUNT(*) as count FROM news_articles GROUP BY category", con=engine)
    print(count_df.to_string(index=False))

except Exception as e:
    print(f"\n❌ DB 적재 실패: AWS RDS 접속 정보를 확인하고 인바운드 보안 그룹(3306 포트)을 확인해 주세요.")
    print(e)
