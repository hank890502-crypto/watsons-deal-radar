# 家用主機（含 ARM64）用：docker build -t watsons-deal-radar . && docker run --rm --env-file .env -v $PWD:/app watsons-deal-radar
# 搭配 host 的 cron 定時執行；或 docker compose run --rm radar
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-m", "radar", "scan", "--publish"]
