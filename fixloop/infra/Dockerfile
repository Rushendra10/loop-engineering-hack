# fixloop worker + API image. One image for both to save build time.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg unzip \
    && rm -rf /var/lib/apt/lists/*

# JavaScript toolchains used by profiled TypeScript repositories.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && corepack enable \
 && rm -rf /var/lib/apt/lists/*

# GitHub CLI (for the PR-opening path; needs GH_TOKEN env at runtime)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
      https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# Official Cursor CLI installer writes cursor-agent to ~/.local/bin.
ENV PATH="/root/.local/bin:/root/.cursor/bin:/root/.bun/bin:${PATH}"

RUN curl https://cursor.com/install -fsS | bash \
 && curl -fsSL https://astral.sh/uv/install.sh | sh \
 && curl -fsSL https://bun.sh/install | bash \
 && cursor-agent --version

RUN pip install --no-cache-dir \
    pytest pyyaml fastapi uvicorn httpx poetry pipenv \
    stripe "x402[fastapi,evm]"

WORKDIR /app
COPY . /app

# Runtime env expected:
#   GH_TOKEN            bot account token (fork + PR only)
#   CURSOR_API_KEY      optional for containers; local workers may browser-login
#   FIXLOOP_CURSOR_MODEL defaults to auto
#   AKASHML_BASE_URL    OpenAI-compatible endpoint
#   AKASHML_API_KEY
# Optional x402 sandbox path:
#   X402_ENABLED=true
#   STRIPE_SECRET_KEY
#   FACILITATOR_URL=https://www.x402.org/facilitator
#   X402_NETWORK=eip155:84532
#   FIXLOOP_PRICE_USDC=0.01

EXPOSE 8080
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8080"]
