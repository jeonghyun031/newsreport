FROM apache/airflow:2.7.1

USER airflow

RUN pip install --no-cache-dir \
    psycopg2-binary \
    selenium \
    webdriver-manager \
    beautifulsoup4 \
    pandas \
    apache-airflow-providers-apache-spark==4.1.3 \
    transformers \
    torch \
    sentencepiece \
    sentence-transformers