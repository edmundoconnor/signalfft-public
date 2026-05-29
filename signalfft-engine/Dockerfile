FROM python:3.12-slim
RUN groupadd -r signalfft && useradd -r -g signalfft signalfft
WORKDIR /app

COPY *.whl ./
RUN pip install --no-cache-dir *.whl

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
COPY prompts/ prompts/
COPY config/ config/

ENV PYTHONPATH=/app/src

USER signalfft

ENTRYPOINT ["python", "-m"]
CMD ["engine.runner_main"]
