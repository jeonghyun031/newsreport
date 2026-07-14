from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType
from pyspark.sql.functions import col, to_timestamp, trim, when, udf
import os

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
env_paths = [".env", "../.env", "/home/jovyan/work/.env"]
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

aws_rds_endpoint = env_dict.get("AWS_RDS_ENDPOINT", os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"))
db_port = env_dict.get("DB_PORT", os.getenv("DB_PORT", "3306"))
db_user = env_dict.get("DB_USER", os.getenv("DB_USER", "admin"))
db_password = env_dict.get("DB_PASSWORD", os.getenv("DB_PASSWORD", "12341234"))
db_name = env_dict.get("DB_NAME", os.getenv("DB_NAME", "test_db"))

jdbc_url = f"jdbc:mysql://{aws_rds_endpoint}:{db_port}/{db_name}?useSSL=false&allowPublicKeyRetrieval=true"
db_properties = {
    "user": db_user,
    "password": db_password,
    "driver": "com.mysql.cj.jdbc.Driver"
}

# 1. 뉴스 CSV 데이터 추출 (Extract)
print("\n📡 뉴스 CSV 데이터를 추출하는 중...")
csv_path = "Crawling/NewsList*daum.csv"
if not os.path.exists("Crawling") and os.path.exists("/home/jovyan/work/Crawling"):
    csv_path = "/home/jovyan/work/Crawling/NewsList*daum.csv"

# CSV 스키마 정의 (5개 컬럼: 날짜, 제목, 언론사, 내용, 주소)
raw_schema = StructType([
    StructField("raw_date", StringType(), True),
    StructField("raw_title", StringType(), True),
    StructField("raw_press", StringType(), True),
    StructField("raw_content", StringType(), True),
    StructField("raw_url", StringType(), True)
])

try:
    df = spark.read \
        .option("header", "false") \
        .option("multiLine", "true") \
        .option("quote", "\"") \
        .option("escape", "\"") \
        .schema(raw_schema) \
        .csv(csv_path)
    
    total_count = df.count()
    print(f"📥 로드된 원본 뉴스 기사 수: {total_count}개")
except Exception as e:
    print(f"❌ CSV 로드 실패. 경로를 확인해주세요: {csv_path}")
    print(e)
    spark.stop()
    exit(1)

# 2. 카테고리 태깅을 위한 UDF 정의 (Transform)
def classify_category(title, content):
    title = title.lower() if title else ""
    content = content.lower() if content else ""
    
    # KBO 야구 핵심 키워드 리스트
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

# 3. 데이터 정제 과정 (Transform)
print("🧹 데이터 정제 및 카테고리 분류 프로세스 가동...")

# Null 및 비정상 데이터 필터링
cleaned_df = df.dropna(subset=["raw_date", "raw_title"])

# 제목, 본문, 언론사, URL 공백 제거 및 트림
cleaned_df = cleaned_df.withColumn("title", trim(col("raw_title")))
cleaned_df = cleaned_df.withColumn("content", trim(col("raw_content")))
cleaned_df = cleaned_df.withColumn("press", when(col("raw_press").isNull(), "미상").otherwise(trim(col("raw_press"))))
cleaned_df = cleaned_df.withColumn("url", when(col("raw_url").isNull(), "").otherwise(trim(col("raw_url"))))

# 날짜 포맷 변환 (2026.07.13 16:57 -> Timestamp)
cleaned_df = cleaned_df.withColumn("date", to_timestamp(col("raw_date"), "yyyy.MM.dd HH:mm"))

# 카테고리 태깅
cleaned_df = cleaned_df.withColumn("category", classify_udf(col("title"), col("content")))

# 필요한 컬럼만 최종 선택 (url 추가)
final_df = cleaned_df.select("date", "title", "press", "content", "category", "url")

# 중복 기사 제거 (제목과 본문 기준)
final_df = final_df.dropDuplicates(subset=["title", "content"])

print(f"✨ 정제 후 중복 제거된 기사 수: {final_df.count()}개")
final_df.show(5, truncate=30)

# 4. AWS MySQL에 'news_articles' 테이블을 생성하며 로드 (Load)
try:
    print(f"\n🔄 AWS MySQL ({db_name}.news_articles)에 테이블 생성 및 데이터 로드 중...")
    
    # MySQL의 본문 텍스트 데이터 보존 및 URL 데이터 공간 할당
    column_types = "date TIMESTAMP, title VARCHAR(500), press VARCHAR(200), content LONGTEXT, category VARCHAR(50), url VARCHAR(1000)"
    
    final_df.write \
        .option("createTableColumnTypes", column_types) \
        .jdbc(url=jdbc_url, table="news_articles", mode="overwrite", properties=db_properties)
        
    print("🎉 AWS MySQL에 'news_articles' 테이블 생성 및 데이터 저장 성공!")
    
    # 5. 검증: MySQL에서 다시 데이터를 읽어와서 카테고리별 통계 출력
    print("\n🔍 [검증] AWS MySQL에서 데이터 통계 조회 (news_articles)")
    mysql_df = spark.read.jdbc(url=jdbc_url, table="news_articles", properties=db_properties)
    mysql_df.groupBy("category").count().show()

except Exception as e:
    print("\n❌ DB 로드 실패: 자격증명, 포트 또는 인바운드 보안그룹 규칙을 확인하세요.")
    print(e)

finally:
    spark.stop()
    print("🛑 Spark Session 종료 완료.")
