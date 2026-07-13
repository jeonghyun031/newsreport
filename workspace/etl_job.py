from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType
from pyspark.sql.functions import col, to_date, trim, when

print("🚀 Spark Session 시작 중...")
spark = SparkSession.builder \
    .appName("AWS_MySQL_News_ETL") \
    .config("spark.jars.packages", "com.mysql:mysql-connector-j:8.3.0") \
    .getOrCreate()

# 1. 요구하신 데이터 행(date, title, 언론사, 내용)에 맞춘 샘플 데이터 정의
raw_news_data = [
    ("2026-07-13", "AI 혁명과 데이터 분석의 미래", "IT기자단", "AI 기술이 급격하게 발전하면서...")
]

# 스키마 정의 (date, title, press, content)
# ※ MySQL 예약어 충돌을 방지하기 위해 '언론사'는 press, '내용'은 content로 변환하여 매핑합니다.
schema = StructType([
    StructField("date", StringType(), True),
    StructField("title", StringType(), True),
    StructField("press", StringType(), True),
    StructField("content", StringType(), True)
])

# 데이터프레임 생성
df = spark.createDataFrame(raw_news_data, schema)
print("\n📊 [1. 정제 전 원본 뉴스 데이터]")
df.show(truncate=False)


# 2. 데이터 정제 과정 (Transform)
print("🧹 데이터 정제 프로세스 가동...")

# (1) 핵심 정보인 날짜(date)나 제목(title)이 없는 유실 데이터 제거
cleaned_df = df.dropna(subset=["date", "title"])

# (2) 기사 제목의 앞뒤 불필요한 공백 제거 (trim)
cleaned_df = cleaned_df.withColumn("title", trim(col("title")))

# (3) 언론사(press) 정보가 누락된 경우 '미상'으로 기본값 채우기
cleaned_df = cleaned_df.withColumn("press", when(col("press").isNull(), "미상").otherwise(col("press")))

# (4) 서로 다른 날짜 포맷(2026-07-13 또는 2026/07/12)을 Spark 표준 Date 타입으로 통일
# 포맷이 안 맞으면 알아서 표준 날짜 형태로 변환해 줍니다.
cleaned_df = cleaned_df.withColumn("date", 
    when(col("date").contains("-"), to_date(col("date"), "yyyy-MM-dd"))
    .otherwise(to_date(col("date"), "yyyy/MM/dd"))
)

# (5) 제목과 내용이 완전히 겹치는 동일한 기사(중복) 제거
cleaned_df = cleaned_df.dropDuplicates(subset=["title", "content"])

print("\n✨ [2. 정제 완료된 뉴스 데이터]")
cleaned_df.show(truncate=False)


# 3. AWS MySQL 연결 설정 (★본인의 AWS 정보로 수정하세요!)
jdbc_url = "jdbc:mysql://your-aws-rds-endpoint.amazonaws.com:3306/your_database"
db_properties = {
    "user": "root",
    "password": "12341234",
    "driver": "com.mysql.cj.jdbc.Driver"
}

# 4. AWS MySQL에 'news_articles' 테이블을 생성하며 로드 (Load)
try:
    print("\n🔄 [3. AWS MySQL에 테이블 생성 및 데이터 로드 중...]")
    cleaned_df.write.jdbc(url=jdbc_url, table="news_articles", mode="overwrite", properties=db_properties)
    print("🎉 AWS MySQL에 'news_articles' 테이블 생성 및 데이터 저장 성공!")
    
    # 5. 잘 들어갔는지 확인차 MySQL에서 다시 데이터를 읽어와 출력해보기
    print("\n🔍 [4. 검증: AWS MySQL에서 데이터 다시 읽어오기]")
    mysql_df = spark.read.jdbc(url=jdbc_url, table="news_articles", properties=db_properties)
    mysql_df.show(truncate=False)

except Exception as e:
    print("\n❌ 에러 발생: AWS 엔드포인트 정보나 3306 포트 보안그룹 설정을 확인하세요.")
    print(e)