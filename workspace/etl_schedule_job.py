from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType
from pyspark.sql.functions import col, trim, when
import os
import sys
from datetime import datetime

print("🚀 KBO 경기 일정 Spark Session 시작 중...")

# Spark 세션 생성 (MySQL JDBC 드라이버 포함)
spark = SparkSession.builder \
    .appName("AWS_MySQL_KBO_Schedule_ETL") \
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

# 공통 실행 파라미터 획득 (년월 단위, 예: 202607)
target_month = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m")

# ==============================================================================
# [Extract] KBO 경기 일정 CSV 파일 경로 정의 및 로드
# ==============================================================================
schedule_csv_path = f"/opt/shared/raw/raw_kbo_schedule_{target_month}.csv"
if not os.path.exists(schedule_csv_path) and os.path.exists(f"/home/jovyan/work/raw/raw_kbo_schedule_{target_month}.csv"):
    schedule_csv_path = f"/home/jovyan/work/raw/raw_kbo_schedule_{target_month}.csv"

print(f"\n📡 KBO 경기 일정 CSV 데이터를 추출하는 중... 대상 파일: {schedule_csv_path}")

# 경기 일정 크롤링 스키마 정의 (크롤러 컬럼 구조에 맞춰 수정 가능)
schedule_schema = StructType([
    StructField("game_date", StringType(), True),
    StructField("game_time", StringType(), True),
    StructField("away_team", StringType(), True),
    StructField("home_team", StringType(), True),
    StructField("stadium", StringType(), True),
    StructField("game_status", StringType(), True)
])

schedule_loaded = False
try:
    if os.path.exists(schedule_csv_path):
        schedule_df = spark.read \
            .option("header", "true") \
            .option("multiLine", "true") \
            .option("quote", "\"") \
            .option("escape", "\"") \
            .schema(schedule_schema) \
            .csv(schedule_csv_path)
        
        total_count = schedule_df.count()
        print(f"📥 로드된 원본 경기 일정 개수: {total_count}개")
        schedule_loaded = True
    else:
        print(f"❌ 경기 일정 파일이 존재하지 않습니다: {schedule_csv_path}")
except Exception as e:
    print(f"❌ CSV 로드 실패: {schedule_csv_path}")
    print(e)

if not schedule_loaded:
    print("❌ 전처리할 경기 일정 데이터가 존재하지 않아 작업을 종료합니다.")
    spark.stop()
    exit(1)


# ==============================================================================
# [Transform] KBO 경기 일정 데이터 정제
# ==============================================================================
print("\n🧹 KBO 경기 일정 데이터 정제 프로세스 가동...")

# 필수 데이터 누락 행 제거 (날짜, 홈/원정팀 필수)
cleaned_df = schedule_df.dropna(subset=["game_date", "away_team", "home_team"])

# 문자열 공백 제거 및 미상 값 처리
cleaned_df = cleaned_df.withColumn("game_date", trim(col("game_date")))
cleaned_df = cleaned_df.withColumn("game_time", when(col("game_time").isNull(), "18:30").otherwise(trim(col("game_time"))))
cleaned_df = cleaned_df.withColumn("away_team", trim(col("away_team")))
cleaned_df = cleaned_df.withColumn("home_team", trim(col("home_team")))
cleaned_df = cleaned_df.withColumn("stadium", when(col("stadium").isNull(), "미정").otherwise(trim(col("stadium"))))
cleaned_df = cleaned_df.withColumn("game_status", when(col("game_status").isNull(), "예정").otherwise(trim(col("game_status"))))

# 최종 컬럼 선택 및 동일 경기 중복 제거
final_schedule_df = cleaned_df.select("game_date", "game_time", "away_team", "home_team", "stadium", "game_status")
final_schedule_df = final_schedule_df.dropDuplicates(subset=["game_date", "game_time", "away_team", "home_team"])

print(f"✨ 정제 완료된 경기 일정 수: {final_schedule_df.count()}개")
final_schedule_df.show(5, truncate=False)


# ==============================================================================
# [Load] KBO 경기 일정 -> AWS MySQL (kbo_schedule 테이블) 적재
# ==============================================================================
target_table_name = "kbo_schedule"

try:
    print(f"\n🔄 AWS MySQL ({db_name}.{target_table_name})에 데이터 로드 중...")
    
    # 경기 일정은 월별로 갱신하거나 덧붙일 수 있도록 mode="overwrite" 적용
    final_schedule_df.write \
        .jdbc(url=jdbc_url, table=target_table_name, mode="overwrite", properties=db_properties)
        
    print(f"🎉 AWS MySQL 내 '{target_table_name}' 테이블에 경기 일정 적재 성공!")
    
    # 적재 결과 확인
    print(f"\n🔍 [검증] MySQL '{target_table_name}' 적재 데이터 조회")
    check_df = spark.read.jdbc(url=jdbc_url, table=target_table_name, properties=db_properties)
    print(f"📊 총 저장된 경기 수: {check_df.count()}개")
    check_df.show(5, truncate=False)

except Exception as e:
    print(f"\n❌ [DB 적재 실패] 테이블 '{target_table_name}' 적재 중 오류가 발생했습니다.")
    print(e)

# 최종 세션 종료
spark.stop()
print("\n🛑 Spark Session 종료 완료.")