FROM apache/airflow:2.7.1

USER root

# Chrome 및 필수 의존성 패키지 설치
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    libgconf-2-4 \
    libnss3 \
    libxss1 \
    libasound2 \
    libxtst6 \
    libgtk-3-0 \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && apt-get clean \
    && rm -rf /var/lib/lists/*

USER airflow

# Python 패키지 설치 (webdriver-manager, selenium, apache-spark 연동 프로바이더 등)
RUN pip install --no-cache-dir \
    selenium \
    webdriver-manager \
    beautifulsoup4 \
    pandas \
    apache-airflow-providers-apache-spark==4.1.3