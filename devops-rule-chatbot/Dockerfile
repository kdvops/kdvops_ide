FROM zricethezav/gitleaks:v8.30.1 AS gitleaks

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m chatbot
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
COPY gitleaks.toml .
COPY app ./app
COPY catalog ./catalog
COPY static ./static
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
