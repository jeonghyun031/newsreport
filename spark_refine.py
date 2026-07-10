# # Git Bash에서 실행 (uv 관리 우회 옵션 포함)
# /c/Users/COMSW/.local/bin/python3.14.exe -m pip install pyspark --break-system-packages
import os
from glob import glob
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, regexp_replace

def main():
    # 1. 로컬 스파크 세션 시작
    spark = SparkSession.builder \
        .appName("DaumNewsRefinePipeline") \
        .master("local[*]") \
        .getOrCreate()
    
    print("🚀 로컬 Apache Spark 세션이 성공적으로 시작되었습니다.")

    # 2. 크롤링 폴더에 쌓인 모든 뉴스 CSV 파일 경로 매칭 (Wildcard 사용)
    input_path = "Crawling/newslist_daum_*.csv"
    
    print(f"📂 수집된 Raw 데이터 로드 중: {input_path}")
    
    # 3. 데이터 로드 (여러 파일이 있어도 스파크가 알아서 하나로 합쳐서 읽습니다)
    try:
        df = spark.read.csv(input_path, header=True, inferSchema=True)
    except Exception as e:
        print(f"❌ 파일을 읽어오는데 실패했습니다. 크롤링 데이터가 있는지 확인하세요: {e}")
        return

    print(f"📊 정제 전 전체 데이터 건수: {df.count()}건")

    # 4. Apache Spark 기반 데이터 정제 (Transform)
    # 내용(본문) 데이터에서 불필요한 공백이나 특수문자가 섞여 있다면 제거하고, 
    # 제목이 완전히 똑같은 중복 기사는 하나만 남기고 날립니다.
    cleaned_df = df \
        .withColumn("내용_정제", regexp_replace(col("내용"), r"[\r\n\t]", " ")) \
        .dropDuplicates(["제목"])

    print(f"✨ 정제 및 중복 제거 후 데이터 건수: {cleaned_df.count()}건")
    cleaned_df.show(5, truncate=30) # 상위 5개 데이터 미리보기

    # 5. 정제된 데이터를 최종 적재 구역(Refined Area)에 저장
    # 실무에서는 여기서 PostgreSQL이나 Supabase DB로 push하게 됩니다.
    # 우선은 로컬에 정제 완료된 CSV 형태로 뽑아볼게요.
    output_dir = "Refined_Data"
    
    # 스파크는 대용량 처리를 위해 데이터를 쪼개서 저장하므로, 
    # 판다스처럼 단일 파일이 아니라 폴더 형태로 결과물이 나옵니다.
    cleaned_df.coalesce(1).write \
        .mode("overwrite") \
        .option("header", "true") \
        .option("encoding", "utf-8-sig") \
        .csv(output_dir)

    print(f"🎉 정제 완료! '{output_dir}' 폴더에 결과가 저장되었습니다.")
    
    # 스파크 세션 종료
    spark.stop()

if __name__ == "__main__":
    main()