"""
Unified runner for Decision + Execution planes.
Starts risk-gateway and execution-router as threads.
"""
import json
import signal
import sys
import logging
import threading
import os

import boto3

logger = logging.getLogger(__name__)


class DecisionExecutionRunner:
    def __init__(self):
        self._shutdown_event = threading.Event()

    def _run_sqs_consumer(self, name: str, service_factory, queue_url_env: str):
        """Generic SQS consumer thread."""
        try:
            logger.info(f"Starting {name}...")
            service = service_factory()
            sqs = boto3.client('sqs', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
            queue_url = os.environ.get(queue_url_env, '')

            if not queue_url:
                logger.error(f"{name}: No queue URL from {queue_url_env}")
                return

            while not self._shutdown_event.is_set():
                try:
                    response = sqs.receive_message(
                        QueueUrl=queue_url,
                        MaxNumberOfMessages=10,
                        WaitTimeSeconds=5,
                    )
                    messages = response.get('Messages', [])

                    if hasattr(service, 'process_batch') and name == "RiskGateway":
                        if messages:
                            try:
                                service.process_batch(messages)
                                for msg in messages:
                                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg['ReceiptHandle'])
                            except Exception as e:
                                logger.error(f"{name}: Batch processing error: {e}", exc_info=True)
                    else:
                        for msg in messages:
                            try:
                                body = json.loads(msg['Body'])
                                service.process_candidate(body)
                                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg['ReceiptHandle'])
                            except Exception as e:
                                logger.error(f"{name}: Message processing error: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"{name}: Poll error: {e}", exc_info=True)
                    self._shutdown_event.wait(5)
        except Exception as e:
            logger.error(f"{name}: Fatal error: {e}", exc_info=True)

    def run(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s [%(threadName)s] %(name)s %(message)s'
        )
        logger.info("SignalFFT Decision-Execution Pipeline starting...")

        def make_risk_gateway():
            from risk_gateway.service import RiskGatewayService
            return RiskGatewayService()

        def make_execution_router():
            from execution.router import ExecutionRouter
            return ExecutionRouter()

        threads = []

        t1 = threading.Thread(
            target=self._run_sqs_consumer,
            args=("RiskGateway", make_risk_gateway, "RISK_INPUT_QUEUE_URL"),
            name="RiskGateway", daemon=True
        )
        t1.start()
        threads.append(t1)

        t2 = threading.Thread(
            target=self._run_sqs_consumer,
            args=("ExecutionRouter", make_execution_router, "EXECUTION_INPUT_QUEUE_URL"),
            name="ExecutionRouter", daemon=True
        )
        t2.start()
        threads.append(t2)

        logger.info(f"All {len(threads)} services started.")

        def shutdown_handler(signum, frame):
            logger.info("Shutdown signal received...")
            self._shutdown_event.set()

        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

        try:
            self._shutdown_event.wait()
        except KeyboardInterrupt:
            self._shutdown_event.set()

        for t in threads:
            t.join(timeout=10)

        logger.info("Decision-Execution Pipeline stopped.")
