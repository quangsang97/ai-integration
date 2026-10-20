FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scrape main.py ./
# GEMINI_API_KEY must be provided as an environment variable at runtime.
CMD ["python", "main.py"]