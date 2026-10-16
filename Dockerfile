FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml /app/
COPY kdvops /app/kdvops
RUN pip install --no-cache-dir . && useradd -u 10001 -m kdvops
USER kdvops
WORKDIR /workspace
ENTRYPOINT ["kdvops"]
CMD ["chat"]
