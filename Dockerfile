FROM python:3.12-slim
ARG KDVOPS_UID=10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml constraints-dev.txt /app/
COPY kdvops /app/kdvops
RUN pip install --no-cache-dir -c constraints-dev.txt . && useradd -u "${KDVOPS_UID}" -m kdvops
USER kdvops
WORKDIR /workspace
ENTRYPOINT ["kdvops"]
CMD ["chat"]
