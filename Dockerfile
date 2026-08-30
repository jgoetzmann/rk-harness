FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
      numpy==2.1.3 scipy==1.14.1 sympy==1.13.3 mpmath==1.3.0 \
      scikit-learn==1.5.2 cma==4.0.0 jsonschema==4.23.0 \
      rich==13.9.4 pytest==8.3.3
# Codex CLI for RK_LLM=codex (HANDOFF §2.2: OAuth done on the host, auth.json mounted :ro).
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
 && npm install -g @openai/codex@0.151.0 \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# No ARM toolchain: the cost model is analytic (HANDOFF §4.5).
# The harness itself is NOT copied in; it is mounted read-only at /harness (HANDOFF §13.2)
# so the verifier cannot be edited from inside the container (test K4).
ENV PYTHONPATH=/harness PYTHONUNBUFFERED=1 RK_WORK_DIR=/work RK_FINDINGS_DIR=/findings
ENTRYPOINT ["/entrypoint.sh"]
