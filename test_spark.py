# spark_process.py 예시
# 📝 추가 연동 팁 (팀원 공유용)
# 지휘관의 원격 신호를 받아 실제 Spark 컨테이너 내부에서 돌게 될 전처리 파이썬 파일(spark_process.py)의 기본 예시 템플릿입니다. 
# 이 스크립트 파일은 공유 폴더 구조상 /opt/shared/scripts/spark_process.py에 위치해 있어야 DAG가 정상 작동합니다
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, when, udf
from pyspark.sql.types import StringType
from datetime import datetime

def main():
    spark = SparkSession.builder.appName("DaumNewsSparkProcessor").getOrCreate()
    news_date = datetime.now().strftime("%Y%m%d")
    
    # 1. 지휘관이 놔두고 간 파일 읽기
    input_path = f"/opt/shared/raw/raw_news_{news_date}.csv"
    df = spark.read.option("header", "true").csv(input_path)
    
    # 2. Spark의 특기를 살려 대용량 분산 '수정/정제' 처리 진행
    # (예시: 제목 40자 제한 처리)
    processed_df = df.withColumn(
        "title", 
        when(col("title").length() > 40, col("title").substr(1, 40).concat("...")).otherwise(col("title"))
    )
    
    # 3. Spark 전용 최종 가공 폴더에 CSV 최종 저장
    output_path = f"/opt/shared/processed/execution_date={news_date}"
    processed_df.coalesce(1).write.mode("overwrite").option("header", "true").csv(output_path)
    
    spark.stop()

if __name__ == "__main__":
    main()