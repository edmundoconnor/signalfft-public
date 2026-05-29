"""
Unified runner for the Intelligence Pipeline.
Starts all intelligence services as daemon threads in a single process.
Each service runs its own SQS polling loop independently.
"""
import signal
import sys
import logging
import threading
import os
import json
import inspect

import boto3

logger = logging.getLogger(__name__)


class IntelligencePipelineRunner:
    def __init__(self):
        self._shutdown_event = threading.Event()

    def _run_service(self, name: str, service_factory):
        """Run a single SQS-consuming service in a thread."""
        try:
            logger.info(f"Starting {name}...")
            service = service_factory()
            sqs = boto3.client('sqs', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
            input_queue_url = getattr(service, 'input_queue_url', None) or getattr(service, '_input_queue_url', None) or os.environ.get('INPUT_QUEUE_URL', '')

            if not input_queue_url:
                logger.warning(f"{name}: No input queue configured, sleeping...")
                self._shutdown_event.wait(30)
                return

            while not self._shutdown_event.is_set():
                try:
                    response = sqs.receive_message(
                        QueueUrl=input_queue_url,
                        MaxNumberOfMessages=10,
                        WaitTimeSeconds=5,
                    )
                    messages = response.get('Messages', [])
                    for msg in messages:
                        try:
                            processed = self._process_message(service, msg)
                            if processed:
                                sqs.delete_message(QueueUrl=input_queue_url, ReceiptHandle=msg['ReceiptHandle'])
                            else:
                                logger.warning("%s: Message processing returned failure; leaving message for retry", name)
                        except Exception as e:
                            logger.error(f"{name}: Failed to process message: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"{name}: Poll cycle error: {e}", exc_info=True)
                    self._shutdown_event.wait(5)
        except Exception as e:
            logger.error(f"{name}: Fatal error: {e}", exc_info=True)

    def _process_message(self, service, msg: dict) -> bool:
        processor = service.process_message if hasattr(service, 'process_message') else service._process_message
        signature = inspect.signature(processor)
        if 'ack' in signature.parameters:
            result = processor(msg, ack=False)
        else:
            result = processor(msg)
        return result is not False

    def _run_periodic_service(self, name: str, service_factory, interval_seconds: int = 300):
        """Run a service that works on a timer (wave engine periodic recompute, attention field)."""
        try:
            logger.info(f"Starting periodic service {name} (interval: {interval_seconds}s)...")
            service = service_factory()

            while not self._shutdown_event.is_set():
                try:
                    if hasattr(service, '_compute_narratives'):
                        service._compute_narratives()
                    elif hasattr(service, '_update_attention_field'):
                        service._update_attention_field()
                except Exception as e:
                    logger.error(f"{name}: Periodic cycle error: {e}", exc_info=True)

                self._shutdown_event.wait(interval_seconds)
        except Exception as e:
            logger.error(f"{name}: Fatal error: {e}", exc_info=True)

    def run(self):
        """Start all intelligence services and block until shutdown."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s [%(threadName)s] %(name)s %(message)s'
        )
        logger.info("SignalFFT Intelligence Pipeline starting...")

        # Import service factories lazily
        def make_feature_extraction():
            from engine.feature_extraction.service import FeatureExtractionService
            return FeatureExtractionService()

        def make_signal_scoring():
            from engine.signal_scoring.service import SignalScoringService
            return SignalScoringService()

        def make_wave_engine():
            from engine.wave_engine.service import WaveEngineService
            return WaveEngineService()

        def make_outcome_tracking():
            from engine.outcome_tracking.service import OutcomeTrackingService
            return OutcomeTrackingService()

        def make_section_extractor():
            from engine.filing_processing.service import SectionExtractorService
            return SectionExtractorService()

        def make_filing_indexer():
            from engine.filing_processing.indexer import FilingIndexerService
            return FilingIndexerService()

        def make_quiet_filing_triage():
            from engine.ai_edges.quiet_filing_triage.service import QuietFilingTriageService
            return QuietFilingTriageService()

        def make_semantic_delta():
            from engine.ai_edges.semantic_delta.service import SemanticDeltaService
            return SemanticDeltaService()

        def make_narrative_gravity():
            from engine.narrative_gravity.service import NarrativeGravityService
            return NarrativeGravityService()

        def make_attention_field():
            from engine.attention_field.service import AttentionFieldService
            return AttentionFieldService()

        # Start SQS-consuming services as threads
        threads = []
        sqs_services = [
            ("FeatureExtraction", make_feature_extraction),
            ("SignalScoring", make_signal_scoring),
            ("WaveEngine", make_wave_engine),
            ("OutcomeTracking", make_outcome_tracking),
            ("SectionExtractor", make_section_extractor),
            ("FilingIndexer", make_filing_indexer),
            ("QuietFilingTriage", make_quiet_filing_triage),
            ("SemanticDelta", make_semantic_delta),
        ]

        for name, factory in sqs_services:
            t = threading.Thread(target=self._run_service, args=(name, factory), name=name, daemon=True)
            t.start()
            threads.append(t)

        # Start periodic services
        periodic_services = [
            ("NarrativeGravity", make_narrative_gravity, 300),
            ("AttentionField", make_attention_field, 600),
        ]

        for name, factory, interval in periodic_services:
            t = threading.Thread(
                target=self._run_periodic_service, args=(name, factory, interval), name=name, daemon=True
            )
            t.start()
            threads.append(t)

        logger.info(f"All {len(threads)} services started.")

        # Handle shutdown
        def shutdown_handler(signum, frame):
            logger.info("Shutdown signal received, stopping all services...")
            self._shutdown_event.set()

        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

        # Block until shutdown
        try:
            self._shutdown_event.wait()
        except KeyboardInterrupt:
            self._shutdown_event.set()

        logger.info("Waiting for threads to finish (10s timeout)...")
        for t in threads:
            t.join(timeout=10)

        logger.info("Intelligence Pipeline stopped.")
