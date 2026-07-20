from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType
from pyspark.sql.functions import col, trim, when
import os
import sys
from datetime import datetime

print("🚀 KBO 통합 일정(선발투수 포함) Spark Session 시작 중...")

# Spark 세션 생성 (MySQL JDBC 드라이버 포함)
spark = SparkSession.builder \
    .appName("AWS_MySQL_KBO_Single_Table_ETL") \
    .config("spark.jars.packages", "com.mysql:mysql-connector-j:8.3.0") \
    .getOrCreate()

# 로그 레벨 설정
spark.sparkContext.setLogLevel("WARN")

# .env 설정 파일 유연한 로드
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

# AWS MySQL 연결 설정
aws_rds_endpoint = env_dict.get("AWS_RDS_ENDPOINT", os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"))
db_port = env_dict.get("DB_PORT", os.getenv("DB_PORT", "3306"))
db_user = env_dict.get("DB_USER", os.getenv("DB_USER", "admin"))
db_password = env_dict.get("DB_PASSWORD", os.getenv("DB_PASSWORD", "12341234"))
db_name = env_dict.get("DB_NAME", os.getenv("DB_NAME", "total_db"))

# ⚠️ 무한 멈춤 방지를 위한 타임아웃 옵션 주입 + SSL 비활성화 보강
jdbc_url = f"jdbc:mysql://{aws_rds_endpoint}:{db_port}/{db_name}?useSSL=false&sslMode=DISABLED&allowPublicKeyRetrieval=true"
db_properties = {
    "user": db_user,
    "password": db_password,
    "driver": "com.mysql.cj.jdbc.Driver",
    "connectTimeout": "60000",
    "socketTimeout": "60000"
}

# 공통 실행 파라미터 획득 (년월 단위, 예: 202607)
target_month = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m")

# ==============================================================================
# [Extract] KBO CSV 파일 로드 (헤더 미포함 구조)
# ==============================================================================
csv_path = f"/opt/shared/raw/raw_kbo_schedule_{target_month}.csv"
if not os.path.exists(csv_path) and os.path.exists(f"/home/jovyan/work/raw/raw_kbo_schedule_{target_month}.csv"):
    csv_path = f"/home/jovyan/work/raw/raw_kbo_schedule_{target_month}.csv"

print(f"\n📡 KBO 데이터를 추출하는 중... 대상 파일: {csv_path}")

# 순수 데이터 정렬 순서대로 8개 컬럼 스키마 정의
integrated_schema = StructType([
    StructField("game_date", StringType(), True),
    StructField("game_time", StringType(), True),
    StructField("away_team", StringType(), True),
    StructField("home_team", StringType(), True),
    StructField("stadium", StringType(), True),
    StructField("status", StringType(), True),        # 크롤러가 던진 6번째 컬럼 수신용
    StructField("away_pitcher", StringType(), True),  # 원정 선발투수
    StructField("home_pitcher", StringType(), True)   # 홈 선발투수
])

try:
    if os.path.exists(csv_path):
        raw_df = spark.read \
            .option("header", "false") \
            .option("multiLine", "true") \
            .option("quote", "\"") \
            .option("escape", "\"") \
            .schema(integrated_schema) \
            .csv(csv_path)
            
        print(f"📥 로드된 원본 데이터 개수: {raw_df.count()}개")
    else:
        print(f"❌ 데이터 파일이 존재하지 않아 작업을 종료합니다: {csv_path}")
        spark.stop()
        exit(1)
except Exception as e:
    print(f"❌ CSV 로드 실패: {e}")
    spark.stop()
    exit(1)


# ==============================================================================
# [Transform] 경기 일정 및 선발투수 데이터 통합 정제
# ==============================================================================
print("\n🧹 KBO 통합 데이터 정제 프로세스 가동...")

# 필수 데이터 누락 행 제거 (날짜, 홈/원정팀 필수)
cleaned_df = raw_df.dropna(subset=["game_date", "away_team", "home_team"])

# 공백 제거 및 기본값 처리
cleaned_df = cleaned_df.withColumn("game_date", trim(col("game_date")))
cleaned_df = cleaned_df.withColumn("game_time", when(col("game_time").isNull() | (trim(col("game_time")) == ""), "18:30").otherwise(trim(col("game_time"))))
cleaned_df = cleaned_df.withColumn("away_team", trim(col("away_team")))
cleaned_df = cleaned_df.withColumn("home_team", trim(col("home_team")))
cleaned_df = cleaned_df.withColumn("stadium", when(col("stadium").isNull() | (trim(col("stadium")) == ""), "미정").otherwise(trim(col("stadium"))))

# 🌟 DB 컬럼 이름에 맞춰 'game_status' 변환 및 데이터 정제 진행
cleaned_df = cleaned_df.withColumn("game_status", when(col("status").isNull() | (trim(col("status")) == ""), "예정").otherwise(trim(col("status"))))

# 선발투수 이름 공백 제거 및 미정/확인불가 예외 처리
cleaned_df = cleaned_df.withColumn("away_pitcher", when(col("away_pitcher").isNull() | (trim(col("away_pitcher")) == "") | (col("away_pitcher") == "확인불가"), "미정").otherwise(trim(col("away_pitcher"))))
cleaned_df = cleaned_df.withColumn("home_pitcher", when(col("home_pitcher").isNull() | (trim(col("home_pitcher")) == "") | (col("home_pitcher") == "확인불가"), "미정").otherwise(trim(col("home_pitcher"))))

# 최종 컬럼 선택 (DB 구조와 100% 일치하도록 순서 고정)
final_df = cleaned_df.select(
    "game_date", "game_time", "away_team", "home_team", 
    "stadium", "game_status", "away_pitcher", "home_pitcher"
)

# 동일 경기 중복 제거
final_df = final_df.dropDuplicates(subset=["game_date", "game_time", "away_team", "home_team"])

print(f"✨ 정제 완료된 총 경기 수: {final_df.count()}개")
final_df.show(5, truncate=False)


# ==============================================================================
# [Load] AWS MySQL 단일 테이블(kbo_schedule)에 데이터 적재
# ==============================================================================
target_table_name = "kbo_schedule"

try:
    print(f"\n🔄 AWS MySQL ({db_name}.{target_table_name})에 통합 데이터 로드 중...")
    
    # 하나의 테이블에 모든 컬럼을 한방에 Overwrite 적재
    final_df.write \
        .format("jdbc") \
        .option("url", jdbc_url) \
        .option("dbtable", target_table_name) \
        .option("user", db_properties["user"]) \
        .option("password", db_properties["password"]) \
        .option("driver", db_properties["driver"]) \
        .option("connectTimeout", "300000") \
        .option("socketTimeout", "300000") \
        .option("truncate", "true") \
        .mode("overwrite") \
        .save()
        
    print(f"🎉 AWS MySQL 내 '{target_table_name}' 테이블에 경기 일정 및 선발투수 데이터 적재 성공!")
    
    # 적재 결과 확인
    print(f"\n🔍 [검증] MySQL '{target_table_name}' 적재 데이터 조회")
    check_df = spark.read.jdbc(url=jdbc_url, table=target_table_name, properties=db_properties)
    print(f"📊 최종 저장된 행 수: {check_df.count()}개")
    check_df.show(5, truncate=False)

except Exception as e:
    print(f"\n❌ [DB 적재 실패] 테이블 '{target_table_name}' 적재 중 오류가 발생했습니다.")
    print(e)

# 최종 세션 종료
spark.stop()
print("\n🛑 Spark Session 종료 완료.")