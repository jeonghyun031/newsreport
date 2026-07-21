from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType
from pyspark.sql.functions import col, to_timestamp, trim, when, udf
import os
import sys
from datetime import datetime, timezone, timedelta

print("🚀 Spark Session 시작 중...")
# Spark 세션 생성 (MySQL JDBC 드라이버 추가 포함)
spark = SparkSession.builder \
    .appName("AWS_MySQL_News_ETL") \
    .config("spark.jars.packages", "com.mysql:mysql-connector-j:8.3.0") \
    .getOrCreate()

# 로그 레벨 설정
spark.sparkContext.setLogLevel("WARN")

# .env 설정 파일 유연한 로드 (로컬 / 컨테이너 양방향 호환)
env_dict = {}
env_paths = [".env", "../.env", "/home/jovyan/work/.env", "/opt/shared/.env"]
env_path = None
for path in env_paths:
    if os.path.exists(path):
        env_path = path
        break

if env_path:
    print(f"📝 설정 파일({env_path})을 로드합니다...")
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_dict[k.strip()] = v.strip()
else:
    print("⚠️ .env 파일을 찾을 수 없어 기본/시스템 환경변수를 사용합니다.")

# AWS MySQL 연결 설정
aws_rds_endpoint = env_dict.get("AWS_RDS_ENDPOINT", os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"))
db_port = env_dict.get("DB_PORT", os.getenv("DB_PORT", "3306"))
db_user = env_dict.get("DB_USER", os.getenv("DB_USER", "admin"))
db_password = env_dict.get("DB_PASSWORD", os.getenv("DB_PASSWORD", "12341234"))
db_name = env_dict.get("DB_NAME", os.getenv("DB_NAME", "total_db"))

jdbc_url = f"jdbc:mysql://{aws_rds_endpoint}:{db_port}/{db_name}?useSSL=false&allowPublicKeyRetrieval=true"
db_properties = {
    "user": db_user,
    "password": db_password,
    "driver": "com.mysql.cj.jdbc.Driver"
}

# 공통 실행 일자 파라미터 획득 (한국 시간 KST 명시적 적용)
kst = timezone(timedelta(hours=9))
target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(kst).strftime("%Y%m%d")

# ==============================================================================
# [Extract 1] 일반 야구 속보 뉴스 파일 경로 정의 및 로드
# ==============================================================================
news_csv_path = f"/opt/shared/raw/raw_news_{target_date}.csv"
if not os.path.exists(news_csv_path) and os.path.exists(f"/home/jovyan/work/raw/raw_news_{target_date}.csv"):
    news_csv_path = f"/home/jovyan/work/raw/raw_news_{target_date}.csv"

print(f"\n📡 [1] 속보 뉴스 CSV 데이터를 추출하는 중... 대상 파일: {news_csv_path}")

news_schema = StructType([
    StructField("raw_date", StringType(), True),
    StructField("raw_press", StringType(), True),
    StructField("raw_title", StringType(), True),
    StructField("raw_content", StringType(), True),
    StructField("raw_url", StringType(), True)
])

news_loaded = False
try:
    news_df = spark.read \
        .option("header", "false") \
        .option("multiLine", "true") \
        .option("quote", "\"") \
        .option("escape", "\"") \
        .schema(news_schema) \
        .csv(news_csv_path)
    
    total_news_count = news_df.count()
    print(f"📥 로드된 원본 속보 뉴스 기사 수: {total_news_count}개")
    news_loaded = True
except Exception as e:
    print(f"⚠️ 속보 CSV 로드 실패. 경로를 확인해주세요: {news_csv_path}")
    print(e)


# ==============================================================================
# [Extract 2] KBO 랭킹 뉴스 파일 경로 정의 및 로드
# ==============================================================================
rank_csv_path = f"/opt/shared/raw/raw_baseball_ranking_{target_date}.csv"
if not os.path.exists(rank_csv_path) and os.path.exists(f"/home/jovyan/work/raw/raw_baseball_ranking_{target_date}.csv"):
    rank_csv_path = f"/home/jovyan/work/raw/raw_baseball_ranking_{target_date}.csv"

print(f"\n📡 [2] KBO 랭킹 뉴스 CSV 데이터를 추출하는 중... 대상 파일: {rank_csv_path}")

rank_schema = StructType([
    StructField("raw_rank", StringType(), True),
    StructField("raw_date", StringType(), True),
    StructField("raw_press", StringType(), True),
    StructField("raw_title", StringType(), True),
    StructField("raw_content", StringType(), True),
    StructField("raw_url", StringType(), True),
    StructField("raw_type", StringType(), True)
])

rank_loaded = False
try:
    if os.path.exists(rank_csv_path):
        rank_df = spark.read \
            .option("header", "false") \
            .option("multiLine", "true") \
            .option("quote", "\"") \
            .option("escape", "\"") \
            .schema(rank_schema) \
            .csv(rank_csv_path)
        
        total_rank_count = rank_df.count()
        print(f"📥 로드된 원본 랭킹 뉴스 기사 수: {total_rank_count}개")
        rank_loaded = True
    else:
        print(f"⚠️ 랭킹 뉴스 파일이 존재하지 않아 수집을 건너뜁니다. (경로: {rank_csv_path})")
except Exception as e:
    print(f"⚠️ 랭킹 CSV 로드 실패: {rank_csv_path}")
    print(e)


if not news_loaded and not rank_loaded:
    print("❌ 전처리할 데이터가 존재하지 않습니다. 작업을 종료합니다.")
    spark.stop()
    exit(1)


# ==============================================================================
# [Transform 1] 카테고리 태깅을 위한 UDF 정의
# ==============================================================================
def classify_category(title, content):
    title = title.lower() if title else ""
    content = content.lower() if content else ""
    
    kbo_keywords = ["kbo", "야구", "한화", "기아", "kia", "두산", "삼성", "롯데", "쓱", "ssg", "키움", "케이티", "kt", "엔씨", "nc", "엘지", "lg", "프로야구"]
    mlb_keywords = ["mlb", "메이저리그", "다저스", "양키스", "샌디에이고", "샌프란시스코", "오타니", "김하성"]
    epl_keywords = ["epl", "토트넘", "맨유", "맨시티", "손흥민"]
    kleague_keywords = ["k리그", "울산", "전북", "포항", "fc서울"]
    
    combined = title + " " + content
    
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

classify_udf = udf(classify_category, StringType())


# ==============================================================================
# [Transform 2] 야구 속보 뉴스 데이터 정제 프로세스
# ==============================================================================
final_news_df = None
if news_loaded:
    print("\n🧹 야구 속보 뉴스 정제 및 카테고리 분류 가동...")
    
    cleaned_news_df = news_df.dropna(subset=["raw_date", "raw_title"])
    cleaned_news_df = cleaned_news_df.withColumn("title", trim(col("raw_title")))
    cleaned_news_df = cleaned_news_df.withColumn("content", trim(col("raw_content")))
    cleaned_news_df = cleaned_news_df.withColumn("press", when(col("raw_press").isNull(), "미상").otherwise(trim(col("raw_press"))))
    cleaned_news_df = cleaned_news_df.withColumn("url", when(col("raw_url").isNull(), "").otherwise(trim(col("raw_url"))))
    cleaned_news_df = cleaned_news_df.withColumn("date", to_timestamp(col("raw_date"), "yyyy.MM.dd HH:mm"))
    cleaned_news_df = cleaned_news_df.withColumn("category", classify_udf(col("title"), col("content")))

    # 📌 요청사항 반영: 순서 [날짜, 언론사, 제목, 본문, 주소, 카테고리]
    final_news_df = cleaned_news_df.select("date", "press", "title", "content", "url", "category")
    
    # 1. 파일 내부 중복 제거 (URL 및 제목 기준)
    final_news_df = final_news_df.dropDuplicates(subset=["url"])
    final_news_df = final_news_df.dropDuplicates(subset=["title"])

    print(f"✨ 1차 파일 내 중복 제거 완료 (속보): {final_news_df.count()}개")


# ==============================================================================
# [Transform 3] KBO 랭킹 뉴스 데이터 정제 프로세스
# ==============================================================================
final_rank_df = None
if rank_loaded:
    print("\n🧹 KBO 랭킹 뉴스 데이터 정제 가동...")
    
    cleaned_rank_df = rank_df.dropna(subset=["raw_rank", "raw_date", "raw_title"])
    cleaned_rank_df = cleaned_rank_df.withColumn("rank", col("raw_rank").cast(IntegerType()))
    cleaned_rank_df = cleaned_rank_df.withColumn("title", trim(col("raw_title")))
    cleaned_rank_df = cleaned_rank_df.withColumn("content", trim(col("raw_content")))
    cleaned_rank_df = cleaned_rank_df.withColumn("press", when(col("raw_press").isNull(), "미상").otherwise(trim(col("raw_press"))))
    cleaned_rank_df = cleaned_rank_df.withColumn("url", when(col("raw_url").isNull(), "").otherwise(trim(col("raw_url"))))
    cleaned_rank_df = cleaned_rank_df.withColumn("date", to_timestamp(col("raw_date"), "yyyy.MM.dd HH:mm"))
    cleaned_rank_df = cleaned_rank_df.withColumn("type", when(col("raw_type").isNull(), "ranking").otherwise(trim(col("raw_type"))))

    # 📌 랭킹 테이블 순서 지정: rank, date, press, title, content, url, type
    final_rank_df = cleaned_rank_df.select("rank", "date", "press", "title", "content", "url", "type")
    
    # 파일 내부 중복 제거
    final_rank_df = final_rank_df.dropDuplicates(subset=["rank", "title"])

    print(f"✨ 1차 파일 내 중복 제거 완료 (랭킹): {final_rank_df.count()}개")


# ==============================================================================
# [Load 1] 야구 속보 뉴스 -> DB 중복 체크 후 Append 적재
# ==============================================================================
if final_news_df and final_news_df.count() > 0:
    table_name = "news_articles"
    try:
        print(f"\n🔄 AWS MySQL ({db_name}.{table_name}) 데이터 체크 및 Append 진행...")
        
        # 📌 2. 기존 DB 테이블 존재 시 이미 있는 기사(URL 기준)를 필터링(Anti-Join)하여 DB 축적 중복 방지
        try:
            existing_db_df = spark.read.jdbc(url=jdbc_url, table=table_name, properties=db_properties)
            # URL 또는 제목이 이미 존재하는 데이터는 제외
            new_news_to_insert = final_news_df.join(
                existing_db_df.select("url"),
                on="url",
                how="left_anti"
            )
        except Exception:
            # 테이블이 아직 생성되지 않은 최초 실행의 경우 전체 데이터 적재
            print(f"ℹ️ '{table_name}' 테이블이 아직 없어 신규 생성합니다.")
            new_news_to_insert = final_news_df

        insert_count = new_news_to_insert.count()
        print(f"📥 DB 신규 추가 대상 기사 수: {insert_count}개")

        if insert_count > 0:
            # 📌 overwrite -> append로 변경하여 누적 저장
            new_news_to_insert.write \
                .jdbc(url=jdbc_url, table=table_name, mode="append", properties=db_properties)
            print(f"🎉 AWS MySQL '{table_name}' 테이블에 {insert_count}건 추가(Append) 완료!")
        else:
            print(f"ℹ️ 모든 속보 기사가 이미 DB에 존재하여 추가 건수가 없습니다.")

    except Exception as e:
        print(f"\n❌ [속보 DB] 적재 실패: {e}")


# ==============================================================================
# [Load 2] KBO 랭킹 뉴스 -> DB 중복 체크 후 Append 적재
# ==============================================================================
if final_rank_df and final_rank_df.count() > 0:
    target_table_name = "kbo_rankings"
    try:
        print(f"\n🔄 랭킹 데이터 DB ({target_table_name}) 체크 및 Append 진행...")
        
        try:
            existing_rank_df = spark.read.jdbc(url=jdbc_url, table=target_table_name, properties=db_properties)
            # URL 또는 (date, rank) 기준 DB 중복 방지 필터링
            new_rank_to_insert = final_rank_df.join(
                existing_rank_df.select("url"),
                on="url",
                how="left_anti"
            )
        except Exception:
            print(f"ℹ️ '{target_table_name}' 테이블이 아직 없어 신규 생성합니다.")
            new_rank_to_insert = final_rank_df

        rank_insert_count = new_rank_to_insert.count()
        print(f"📥 DB 신규 추가 대상 랭킹 기사 수: {rank_insert_count}개")

        if rank_insert_count > 0:
            # 📌 overwrite -> append로 변경하여 누적 저장
            new_rank_to_insert.write \
                .jdbc(url=jdbc_url, table=target_table_name, mode="append", properties=db_properties)
            print(f"🎉 랭킹 데이터 '{target_table_name}' 테이블에 {rank_insert_count}건 추가(Append) 완료!")
        else:
            print(f"ℹ️ 모든 랭킹 기사가 이미 DB에 존재하여 추가 건수가 없습니다.")

    except Exception as e:
        print(f"\n❌ [랭킹 DB] 적재 실패: {e}")


# 최종 세션 종료
spark.stop()
print("\n🛑 Spark Session 종료 완료.")