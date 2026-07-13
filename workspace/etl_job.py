# AWS MySQL Spark Connection Template
# 이 코드를 복사하여 VS Code에 붙여넣고, AWS RDS 엔드포인트 주소를 입력하여 사용하세요.

from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType
from pyspark.sql.functions import col, to_date, trim, when

print("🚀 Spark Session 시작 중...")
# Spark 세션 생성
spark = SparkSession.builder \
    .appName("AWS_MySQL_News_ETL") \
    .getOrCreate()

# 로그 레벨을 WARN(경고) 또는 ERROR(에러)로 설정하여 불필요한 INFO 로그를 숨깁니다.
spark.sparkContext.setLogLevel("WARN")

# 1. 정제 전 샘플 데이터 정의
raw_news_data = [
    ("2026-07-13", "AI 혁명과 데이터 분석의 미래", "IT기자단", "AI 기술이 급격하게 발전하면서...")
]

# 스키마 정의 (date, title, press, content)
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
cleaned_df = df.dropna(subset=["date", "title"])
cleaned_df = cleaned_df.withColumn("title", trim(col("title")))
cleaned_df = cleaned_df.withColumn("press", when(col("press").isNull(), "미상").otherwise(col("press")))
cleaned_df = cleaned_df.withColumn("date", 
    when(col("date").contains("-"), to_date(col("date"), "yyyy-MM-dd"))
    .otherwise(to_date(col("date"), "yyyy/MM/dd"))
)
cleaned_df = cleaned_df.dropDuplicates(subset=["title", "content"])

print("\n✨ [2. 정제 완료된 뉴스 데이터]")
cleaned_df.show(truncate=False)

# 3. AWS MySQL 연결 설정 (★ 본인의 VS Code 세팅 정보로 수정해 주세요!)
aws_rds_endpoint = "your-aws-rds-endpoint.amazonaws.com"
db_name = "test_db"
jdbc_url = f"jdbc:mysql://{aws_rds_endpoint}:3306/{db_name}"

db_properties = {
    "user": "admin",             # 사용자 admin 설정
    "password": "12341234",      # 입력하신 비밀번호
    "driver": "com.mysql.cj.jdbc.Driver"
}

# 4. AWS MySQL에 'news_articles' 테이블을 생성하며 로드 (Load)
try:
    print("\n🔄 [3. AWS MySQL에 테이블 생성 및 데이터 로드 중...]")
    cleaned_df.write.jdbc(url=jdbc_url, table="news_articles", mode="overwrite", properties=db_properties)
    print("🎉 AWS MySQL에 'news_articles' 테이블 생성 및 데이터 저장 성공!")
    
    # 5. 검증: MySQL에서 다시 데이터를 읽어와 출력해보기
    print("\n🔍 [4. 검증: AWS MySQL에서 데이터 다시 읽어오기]")
    mysql_df = spark.read.jdbc(url=jdbc_url, table="news_articles", properties=db_properties)
    mysql_df.show(truncate=False)

except Exception as e:
    print("\n❌ 에러 발생: AWS 엔드포인트 정보, 3306 포트의 보안그룹(인바운드 규칙) 설정을 확인하세요.")
    print(e)
