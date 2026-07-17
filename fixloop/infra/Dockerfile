# fixloop worker + API image. One image for both to save build time.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (for the PR-opening path; needs GH_TOKEN env at runtime)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
      https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest pyyaml fastapi uvicorn httpx

# --- Cursor agent CLI ---------------------------------------------------
# TODO(P2/P3): verify the current install command with the Cursor booth or
# docs before building -- do NOT trust this line blindly, it may be stale:
#   RUN curl -fsSL https://cursor.com/install | bash
# If the installer needs a login token, bake it via a runtime env var
# (CURSOR_API_KEY or equivalent), never into the image.

WORKDIR /app
COPY . /app

# Runtime env expected:
#   GH_TOKEN            bot account token (fork + PR only)
#   AKASHML_BASE_URL    OpenAI-compatible endpoint
#   AKASHML_API_KEY
#   FIXLOOP_PRICE_USDC  advertised per-call price (display only)

EXPOSE 8080
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8080"]
