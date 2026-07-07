FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY banner.jpg .

VOLUME ["/app"]

CMD ["python", "bot.py"]
