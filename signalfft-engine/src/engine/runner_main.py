"""Entry point: python -m engine.runner_main"""
from engine.runner import IntelligencePipelineRunner

if __name__ == "__main__":
    runner = IntelligencePipelineRunner()
    runner.run()
