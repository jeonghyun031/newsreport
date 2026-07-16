from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType
from pyspark.sql.functions import col, to_timestamp, trim, when, udf
import os
import sys
from datetime import datetime

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
db_name = env_dict.get("DB_NAME", os.getenv("DB_NAME", "articel_db"))

jdbc_url = f"jdbc:mysql://{aws_rds_endpoint}:{db_port}/{db_name}?useSSL=false&allowPublicKeyRetrieval=true"
db_properties = {
    "user": db_user,
    "password": db_password,
    "driver": "com.mysql.cj.jdbc.Driver"
}

# 공통 실행 일자 파라미터 획득
target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")

# ==============================================================================
# [Extract 1] 일반 야구 속보 뉴스 파일 경로 정의 및 로드
# ==============================================================================
news_csv_path = f"/opt/shared/raw/raw_news_{target_date}.csv"
if not os.path.exists(news_csv_path) and os.path.exists(f"/home/jovyan/work/raw/raw_news_{target_date}.csv"):
    news_csv_path = f"/home/jovyan/work/raw/raw_news_{target_date}.csv"

print(f"\n📡 [1] 속보 뉴스 CSV 데이터를 추출하는 중... 대상 파일: {news_csv_path}")

news_schema = StructType([
    StructField("raw_date", StringType(), True),
    StructField("raw_title", StringType(), True),
    StructField("raw_press", StringType(), True),
    StructField("raw_content", StringType(), True),
    StructField("raw_url", StringType(), True)
])

news_loaded = False
try:
    news_df = spark.read \
        .option("header", "true") \
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

# 랭킹 CSV 전용 스키마 정의 (rank, date, title, media, content, url, type)
rank_schema = StructType([
    StructField("raw_rank", StringType(), True),
    StructField("raw_date", StringType(), True),
    StructField("raw_title", StringType(), True),
    StructField("raw_press", StringType(), True),
    StructField("raw_content", StringType(), True),
    StructField("raw_url", StringType(), True),
    StructField("raw_type", StringType(), True)
])

rank_loaded = False
try:
    if os.path.exists(rank_csv_path):
        rank_df = spark.read \
            .option("header", "true") \
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


# 두 소스 모두 로드 실패 시 세션 종료
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

    final_news_df = cleaned_news_df.select("date", "title", "press", "content", "category", "url")
    final_news_df = final_news_df.dropDuplicates(subset=["title", "content"])

    print(f"✨ 정제 및 중복 제거 완료 (속보): {final_news_df.count()}개")
    final_news_df.show(3, truncate=30)


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

    # 랭킹 테이블 전용 최종 데이터 프레임 구성
    final_rank_df = cleaned_rank_df.select("rank", "date", "title", "press", "content", "url", "type")
    final_rank_df = final_rank_df.dropDuplicates(subset=["rank", "title"]) # 동일 랭킹 및 동일 제목 중복 필터링

    print(f"✨ 정제 및 중복 제거 완료 (랭킹): {final_rank_df.count()}개")
    final_rank_df.show(3, truncate=30)


# ==============================================================================
# [Load 1] 야구 속보 뉴스 -> 기존 AWS MySQL 적재
# ==============================================================================
if final_news_df:
    try:
        print(f"\n🔄 AWS MySQL ({db_name}.news_articles)에 속보 데이터 로드 중...")
        
        final_news_df.write \
            .jdbc(url=jdbc_url, table="news_articles", mode="overwrite", properties=db_properties)
            
        print("🎉 AWS MySQL에 'news_articles' 테이블 생성 및 속보 데이터 저장 성공!")
        
        # 간단 검증 출력
        print("\n🔍 [검증] AWS MySQL 속보 카테고리별 통계 조회 (news_articles)")
        mysql_df = spark.read.jdbc(url=jdbc_url, table="news_articles", properties=db_properties)
        mysql_df.groupBy("category").count().show()

    except Exception as e:
        print("\n❌ [속보 DB] 적재 실패. 연결 및 테이블 사양을 확인하세요.")
        print(e)


# ==============================================================================
# [Load 2] KBO 랭킹 뉴스 -> 🌟추후 지정 전용 영역 (현재 주석 처리)🌟
# ==============================================================================
if final_rank_df:
    print("\n🚧 [랭킹 DB] 적재 대상 DB 및 타겟 테이블 미지정 상태 (추후 지정 예정)")
    print("📋 현재 정제 완료된 랭킹 데이터 스키마:")
    final_rank_df.printSchema()
    
    # --------------------------------------------------------------------------
    # [새로운 테이블 생성 및 적재 코드]
    # --------------------------------------------------------------------------
    # 💡 꿀팁: 테이블이 존재하지 않는다면, Spark가 자동으로 스키마를 참고하여 테이블을 새로 만듭니다.
    target_table_name = "kbo_rankings"  # 👈 여기에 새로 만들고 싶은 테이블 이름을 입력하세요!
    
    try:
        print(f"🔄 랭킹 데이터를 article_db 내의 새로운 테이블 ({target_table_name})에 로드하는 중...")
        
        # 'overwrite'는 기존에 테이블이 혹시 있다면 덮어쓰며 새로 만들고, 
        # 'append'는 매번 실행할 때마다 데이터를 아래에 덧붙입니다. 상황에 맞게 골라보세요!
        final_rank_df.write \
            .jdbc(url=jdbc_url, table=target_table_name, mode="overwrite", properties=db_properties)
            
        print(f"🎉 랭킹 데이터를 '{target_table_name}' 테이블에 적재 및 생성을 완료했습니다!")
        
    except Exception as e:
        print(f"\n❌ [랭킹 DB] '{target_table_name}' 테이블 적재 실패:")
        print(e)


# 최종 세션 종료
spark.stop()
print("\n🛑 Spark Session 종료 완료.")