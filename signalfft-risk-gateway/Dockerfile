FROM python:3.14-slim
RUN groupadd -r signalfft && useradd -r -g signalfft signalfft

WORKDIR /app

COPY *.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

USER signalfft

ENTRYPOINT ["python", "-m"]
CMD ["risk_gateway"]
