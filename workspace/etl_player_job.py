from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, DoubleType
from pyspark.sql.functions import col, trim, when
import os
import sys
import traceback

# ==============================================================================
# [설정] .env 설정 파일 유연한 로드 (로컬 / 컨테이너 환경 양방향 호환)
# ==============================================================================
env_dict = {}
env_paths = [".env", "../.env", "/home/jovyan/work/.env", "/opt/shared/.env"]
env_path = None
for path in env_paths:
    if os.path.exists(path):
        env_path = path
        break

if env_path:
    print(f"📝 [DEBUG] 설정 파일({env_path})을 발견하여 로드합니다...")
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_dict[k.strip()] = v.strip()
else:
    print("⚠️ [DEBUG] .env 파일을 찾을 수 없어 기본/시스템 환경변수를 사용합니다.")

# AWS MySQL 연결 설정 (환경변수 또는 기본값 사용)
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

print(f"📡 [DEBUG] 데이터베이스 연결 정보:")
print(f"   - Endpoint: {aws_rds_endpoint}")
print(f"   - Port: {db_port}")
print(f"   - User: {db_user}")
print(f"   - Database: {db_name}")

# ==============================================================================
# [Spark 세션 시작]
# ==============================================================================
print("🚀 [DEBUG] Spark Session 시작 중...")
spark = SparkSession.builder \
    .appName("KBO_Player_ETL") \
    .config("spark.jars.packages", "com.mysql:mysql-connector-j:8.3.0") \
    .getOrCreate()

# 로그 레벨 설정 (너무 많은 시스템 로그 방지)
spark.sparkContext.setLogLevel("WARN")
print("✅ [DEBUG] Spark Session 생성 완료.")

# ==============================================================================
# [설정] 대상 CSV 파일 경로 정의 (타자 / 투수 각각 생성)
# ==============================================================================
target_month = sys.argv[1] if len(sys.argv) > 1 else "data"

def resolve_file_path(filename):
    primary = f"/opt/shared/players/{filename}"
    alternative = f"/home/jovyan/work/players/{filename}"
    if os.path.exists(primary):
        return primary
    elif os.path.exists(alternative):
        return alternative
    return primary

hitter_csv_path = resolve_file_path(f"hitter_stats_{target_month}.csv")
pitcher_csv_path = resolve_file_path(f"pitcher_stats_{target_month}.csv")

# ==============================================================================
# [Schema] 타자 및 투수 스키마 정의
# ==============================================================================
hitter_schema = StructType([
    StructField("name", StringType(), True),
    StructField("birth", StringType(), True),
    StructField("date", StringType(), True),
    StructField("opp", StringType(), True),
    StructField("avg1", DoubleType(), True),
    StructField("avg2", DoubleType(), True),
    StructField("r", IntegerType(), True),
    StructField("h", IntegerType(), True),
    StructField("2b", IntegerType(), True),
    StructField("hr", IntegerType(), True),
    StructField("so", IntegerType(), True),
    StructField("gdp", IntegerType(), True)
])

# 투수 스키마 정의 (크롤링 결과 컬럼: name, birth, date, era1, h, hr, era2)
pitcher_schema = StructType([
    StructField("name", StringType(), True),
    StructField("birth", StringType(), True),
    StructField("date", StringType(), True),
    StructField("era1", DoubleType(), True),
    StructField("h", IntegerType(), True),
    StructField("hr", IntegerType(), True),
    StructField("era2", DoubleType(), True)
])

# ==============================================================================
# [Transformation] 데이터 정제 함수 정의
# ==============================================================================
def clean_hitter_data(df):
    target_cols = ["r", "h", "2b", "hr", "so", "gdp"]
    for c in target_cols:
        df = df.withColumn(c, when(col(c).isNull() | (col(c) == "-") | (trim(col(c)) == ""), 0).otherwise(col(c).cast("int")))
    
    avg_cols = ["avg1", "avg2"]
    for c in avg_cols:
        df = df.withColumn(c, when(col(c).isNull() | (col(c) == "-") | (trim(col(c)) == ""), 0.0).otherwise(col(c).cast("double")))
    return df

# [추가] 투수 데이터 정제 함수
def clean_pitcher_data(df):
    int_cols = ["h", "hr"]
    for c in int_cols:
        df = df.withColumn(c, when(col(c).isNull() | (col(c) == "-") | (trim(col(c)) == ""), 0).otherwise(col(c).cast("int")))
    
    double_cols = ["era1", "era2"]
    for c in double_cols:
        df = df.withColumn(c, when(col(c).isNull() | (col(c) == "-") | (trim(col(c)) == ""), 0.0).otherwise(col(c).cast("double")))
    return df

# [공통] DB 적재 Helper 함수
def load_to_mysql(df, target_table):
    print(f"🔄 [DEBUG] MySQL 테이블 '{target_table}'에 데이터 적재 시작 (Mode: Overwrite)...")
    df.write \
        .format("jdbc") \
        .option("url", jdbc_url) \
        .option("dbtable", target_table) \
        .option("user", db_properties["user"]) \
        .option("password", db_properties["password"]) \
        .option("driver", db_properties["driver"]) \
        .mode("overwrite") \
        .save()
    print(f"🎉 [DEBUG] '{target_table}' 테이블 적재 성공!")

# ==============================================================================
# [ETL 프로세스 실행]
# ==============================================================================
try:
    # --------------------------------------------------------------------------
    # 1. 타자(Hitter) ETL
    # --------------------------------------------------------------------------
    print("\n--- 🏏 [1/2] 타자 데이터 ETL 시작 ---")
    if os.path.exists(hitter_csv_path):
        print(f"📂 [DEBUG] 타자 CSV 로딩: {hitter_csv_path}")
        hitter_raw = spark.read.option("header", "true").schema(hitter_schema).csv(hitter_csv_path)
        print(f"📊 [DEBUG] 타자 Raw 데이터: {hitter_raw.count()}건")
        
        hitter_final = clean_hitter_data(hitter_raw)
        hitter_final.show(3, truncate=False)
        load_to_mysql(hitter_final, "hitter_stats")
    else:
        print(f"⚠️ [WARNING] 타자 파일이 존재하지 않아 스킵합니다: {hitter_csv_path}")

    # --------------------------------------------------------------------------
    # 2. 투수(Pitcher) ETL
    # --------------------------------------------------------------------------
    print("\n--- 🥎 [2/2] 투수 데이터 ETL 시작 ---")
    if os.path.exists(pitcher_csv_path):
        print(f"📂 [DEBUG] 투수 CSV 로딩: {pitcher_csv_path}")
        pitcher_raw = spark.read.option("header", "true").schema(pitcher_schema).csv(pitcher_csv_path)
        print(f"📊 [DEBUG] 투수 Raw 데이터: {pitcher_raw.count()}건")
        
        pitcher_final = clean_pitcher_data(pitcher_raw)
        pitcher_final.show(3, truncate=False)
        load_to_mysql(pitcher_final, "pitcher_stats")
    else:
        print(f"⚠️ [WARNING] 투수 파일이 존재하지 않아 스킵합니다: {pitcher_csv_path}")

except Exception as e:
    print(f"❌ [DEBUG] ETL 작업 중 에러 발생: {e}")
    traceback.print_exc()

finally:
    print("\n🧹 [DEBUG] Spark Session을 중지하고 자원을 반납합니다.")
    spark.stop()