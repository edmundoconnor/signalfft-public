"""python -m risk_gateway"""
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [RiskGateway] %(name)s %(message)s",
)

from risk_gateway.service import RiskGatewayService

RiskGatewayService().run()
