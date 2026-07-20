from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, DoubleType
from pyspark.sql.functions import col, trim, when, lit
import os
import sys

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
    .appName("KBO_Hitter_ETL") \
    .config("spark.jars.packages", "com.mysql:mysql-connector-j:8.3.0") \
    .getOrCreate()

# 로그 레벨 설정 (너무 많은 시스템 로그 방지)
spark.sparkContext.setLogLevel("WARN")
print("✅ [DEBUG] Spark Session 생성 완료.")

# ==============================================================================
# [설정] 대상 CSV 파일 경로 정의
# ==============================================================================
# 외부 파라미터가 있으면 가져오고, 없으면 기본값인 "data" 사용
target_month = sys.argv[1] if len(sys.argv) > 1 else "data"
csv_path = f"/opt/shared/players/hitter_stats_{target_month}.csv"

print(f"📂 [DEBUG] 대상 파일 경로: {csv_path}")

# ==============================================================================
# [Schema] 타자 데이터 스키마 정의 (8개 항목 + 기본 정보)
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

# ==============================================================================
# [Transformation] 데이터 정제 함수 정의
# ==============================================================================
def clean_hitter_data(df):
    print("🛠️ [DEBUG] 데이터 정제 작업 시작...")
    
    # 정수형 컬럼 정제: '-' 또는 결측값(Null)을 0으로 대체한 뒤 int 캐스팅
    target_cols = ["r", "h", "2b", "hr", "so", "gdp"]
    for c in target_cols:
        df = df.withColumn(c, when(col(c).isNull() | (col(c) == "-") | (trim(col(c)) == ""), 0).otherwise(col(c).cast("int")))
    
    # 실수형 컬럼(타율 등) 정제: '-' 또는 결측값을 0.0으로 대체한 뒤 double 캐스팅
    avg_cols = ["avg1", "avg2"]
    for c in avg_cols:
        df = df.withColumn(c, when(col(c).isNull() | (col(c) == "-") | (trim(col(c)) == ""), 0.0).otherwise(col(c).cast("double")))
        
    print("✅ [DEBUG] 데이터 정제 작업 완료.")
    return df

# ==============================================================================
# [ETL 프로세스 실행]
# ==============================================================================
try:
    # 1. 파일 존재 여부 먼저 확인
    # 로컬 경로 및 컨테이너 경로 양쪽 다 유연하게 대처
    alternative_path = f"/home/jovyan/work/players/hitter_stats_{target_month}.csv"
    if not os.path.exists(csv_path):
        if os.path.exists(alternative_path):
            print(f"🔄 [DEBUG] 기본 경로에 파일이 없어 대체 경로를 사용합니다: {alternative_path}")
            csv_path = alternative_path
        else:
            raise FileNotFoundError(f"❌ 수집된 CSV 파일을 찾을 수 없습니다. 경로를 확인해주세요. (시도한 경로: {csv_path}, {alternative_path})")

    # 2. 데이터 읽기 (Extract)
    print(f"📖 [DEBUG] CSV 파일에서 raw 데이터 로딩 중...")
    raw_df = spark.read.option("header", "true").schema(hitter_schema).csv(csv_path)
    
    raw_count = raw_df.count()
    print(f"📊 [DEBUG] 로드된 Raw 데이터 개수: {raw_count}개")
    if raw_count == 0:
        print("⚠️ [WARNING] 가져온 데이터가 0건입니다. 파일 내용을 확인해보세요.")

    # 3. 데이터 정제 (Transform)
    final_df = clean_hitter_data(raw_df)
    
    # 디버깅용: 정제 후 상위 5건 콘솔 출력
    print("✨ [DEBUG] 정제 완료 데이터 (상위 5건):")
    final_df.show(5, truncate=False)

    # 4. DB 적재 (Load)
    target_table = "hitter_stats"
    print(f"🔄 [DEBUG] MySQL 테이블 '{target_table}'에 데이터 적재 시작 (Mode: Overwrite)...")
    
    final_df.write \
        .format("jdbc") \
        .option("url", jdbc_url) \
        .option("dbtable", target_table) \
        .option("user", db_properties["user"]) \
        .option("password", db_properties["password"]) \
        .option("driver", db_properties["driver"]) \
        .mode("overwrite") \
        .save()
        
    print(f"🎉 [DEBUG] '{target_table}' 테이블에 데이터 적재 완료 성공!")

except Exception as e:
    print(f"❌ [DEBUG] ETL 작업 중 에러 발생: {e}")
    # 에러 스택 트레이스 세부 출력을 위해 예외 객체를 그대로 출력
    import traceback
    traceback.print_exc()

finally:
    print("🧹 [DEBUG] Spark Session을 중지하고 자원을 반납합니다.")
    spark.stop()