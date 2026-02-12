FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8765

ENV NEXUS_SIMULATION_MODE=true
ENV NEXUS_DB_PATH=/data/nexus.db

CMD ["python3", "main.py"]
