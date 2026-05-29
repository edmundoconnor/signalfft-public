"""Entry point: python -m risk_gateway.unified_main"""
from risk_gateway.unified_runner import DecisionExecutionRunner

if __name__ == "__main__":
    runner = DecisionExecutionRunner()
    runner.run()
