FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8765

ENV OVERWATCH_SIMULATION_MODE=true
ENV OVERWATCH_DB_PATH=/data/overwatch.db

CMD ["python3", "main.py"]
