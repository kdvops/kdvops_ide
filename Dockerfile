FROM python:3.12-slim-bookworm@sha256:62ed8e07e544e8555c5d93748d5275015136b7f5c52b5e8679b4de339c3779d3
ARG KDVOPS_UID=10001
ARG KDVOPS_PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"
ARG KUBECTL_VERSION=1.33.6-1.1
ARG POWERSHELL_VERSION=7.5.4
ARG TARGETARCH
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml constraints-dev.txt /app/
COPY kdvops /app/kdvops
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl gnupg libicu72 libssl3 libunwind8 zlib1g \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL "https://pkgs.k8s.io/core:/stable:/v1.33/deb/Release.key" -o /tmp/kubernetes-release.key \
    && echo "7627818cf7bae52f9008c93e8b1f961f53dea11d40891778de216fb1b43be54d  /tmp/kubernetes-release.key" \
       | sha256sum -c - \
    && gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg /tmp/kubernetes-release.key \
    && echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.33/deb/ /" \
       > /etc/apt/sources.list.d/kubernetes.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "kubectl=${KUBECTL_VERSION}" \
    && case "${TARGETARCH}" in \
         amd64) powershell_arch=x64; powershell_sha=1fd7983fe56ca9e6233f126925edb24bf6b6b33e356b69996d925c4db94e2fef ;; \
         arm64) powershell_arch=arm64; powershell_sha=4b32d4cb86a43dfb83d5602d0294295bf22fafbf9e0785d1aaef81938cda92f8 ;; \
         *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v${POWERSHELL_VERSION}/powershell-${POWERSHELL_VERSION}-linux-${powershell_arch}.tar.gz" \
       -o /tmp/powershell.tar.gz \
    && echo "${powershell_sha}  /tmp/powershell.tar.gz" | sha256sum -c - \
    && install -d -m 0755 /opt/microsoft/powershell/7 \
    && tar -xzf /tmp/powershell.tar.gz -C /opt/microsoft/powershell/7 \
    && chmod 0755 /opt/microsoft/powershell/7/pwsh \
    && ln -s /opt/microsoft/powershell/7/pwsh /usr/local/bin/pwsh \
    && rm -rf /var/lib/apt/lists/* /tmp/kubernetes-release.key /tmp/powershell.tar.gz \
    && echo "WARNING: pip TLS verification disabled for: ${KDVOPS_PIP_TRUSTED_HOST}" \
    && PIP_TRUSTED_HOST="${KDVOPS_PIP_TRUSTED_HOST}" \
       pip install --no-cache-dir -c constraints-dev.txt . \
    && useradd -u "${KDVOPS_UID}" -m kdvops \
    && install -d -o kdvops -g kdvops -m 0700 /home/kdvops/.kube \
    && install -d -o kdvops -g kdvops -m 0700 /home/kdvops/.local/share/kdvops
USER kdvops
WORKDIR /workspace
ENTRYPOINT ["kdvops"]
CMD ["chat"]
