# 1. 베이스 이미지 (버전은 본인이 사용하는 airflow 버전에 맞춰 수정 가능)
FROM apache/airflow:2.8.1

# 2. root 권한으로 크롬(Chromium) 브라우저 설치
USER root
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 3. 다시 airflow 유저로 돌아와서 파이썬 패키지 설치
USER airflow
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt