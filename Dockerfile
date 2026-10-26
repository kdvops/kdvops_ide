FROM python:3.12-slim
ARG KDVOPS_UID=10001
ARG KDVOPS_PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml constraints-dev.txt /app/
COPY kdvops /app/kdvops
RUN echo "WARNING: pip TLS verification disabled for: ${KDVOPS_PIP_TRUSTED_HOST}" \
    && PIP_TRUSTED_HOST="${KDVOPS_PIP_TRUSTED_HOST}" \
       pip install --no-cache-dir -c constraints-dev.txt . \
    && useradd -u "${KDVOPS_UID}" -m kdvops
USER kdvops
WORKDIR /workspace
ENTRYPOINT ["kdvops"]
CMD ["chat"]
