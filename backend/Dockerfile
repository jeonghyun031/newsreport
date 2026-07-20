FROM python:3.10-slim

WORKDIR /app

# 백엔드 실행에 필요한 핵심 패키지 설치
RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    sqlalchemy \
    pymysql \
    python-dotenv \
    requests \
    cryptography \
    openai

# 소스 코드 복사 (빌드 컨텍스트가 루트이므로 backend/ 폴더 지정)
COPY backend/ .
COPY saju.py .
COPY deep_learning/ ./deep_learning/

EXPOSE 8000

# FastAPI 구동 명령어
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
