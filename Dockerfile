FROM python:3.12-slim-bookworm@sha256:62ed8e07e544e8555c5d93748d5275015136b7f5c52b5e8679b4de339c3779d3
ARG KDVOPS_UID=10001
ARG KDVOPS_PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"
ARG KUBECTL_VERSION=1.33.6-1.1
ARG POWERSHELL_VERSION=7.5.4-1.deb
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml constraints-dev.txt /app/
COPY kdvops /app/kdvops
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc -o /tmp/microsoft.asc \
    && echo "2fa9c05d591a1582a9aba276272478c262e95ad00acf60eaee1644d93941e3c6  /tmp/microsoft.asc" \
       | sha256sum -c - \
    && gpg --dearmor -o /etc/apt/keyrings/microsoft-prod.gpg /tmp/microsoft.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/microsoft-prod.list \
    && curl -fsSL "https://pkgs.k8s.io/core:/stable:/v1.33/deb/Release.key" -o /tmp/kubernetes-release.key \
    && echo "7627818cf7bae52f9008c93e8b1f961f53dea11d40891778de216fb1b43be54d  /tmp/kubernetes-release.key" \
       | sha256sum -c - \
    && gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg /tmp/kubernetes-release.key \
    && echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.33/deb/ /" \
       > /etc/apt/sources.list.d/kubernetes.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       "kubectl=${KUBECTL_VERSION}" \
       "powershell=${POWERSHELL_VERSION}" \
    && rm -rf /var/lib/apt/lists/* /tmp/microsoft.asc /tmp/kubernetes-release.key \
    && echo "WARNING: pip TLS verification disabled for: ${KDVOPS_PIP_TRUSTED_HOST}" \
    && PIP_TRUSTED_HOST="${KDVOPS_PIP_TRUSTED_HOST}" \
       pip install --no-cache-dir -c constraints-dev.txt . \
    && useradd -u "${KDVOPS_UID}" -m kdvops \
    && install -d -o kdvops -g kdvops -m 0700 /home/kdvops/.kube
USER kdvops
WORKDIR /workspace
ENTRYPOINT ["kdvops"]
CMD ["chat"]
