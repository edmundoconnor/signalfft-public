"""SignalFFT configuration helpers."""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import boto3

logger = logging.getLogger(__name__)


def get_secret_env(name: str, parameter_name_env: str | None = None) -> str:
    """Return a secret from an env var, or from an SSM parameter named by env."""
    value = os.environ.get(name, "")
    if value:
        return value

    param_env = parameter_name_env or f"{name}_PARAM"
    parameter_name = os.environ.get(param_env, "")
    if not parameter_name:
        return ""

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_REGION_NAME")
        or "us-east-1"
    )
    try:
        return _get_ssm_parameter(parameter_name, region)
    except Exception:
        logger.exception("Failed to load secret from SSM parameter %s", parameter_name)
        return ""


@lru_cache(maxsize=32)
def _get_ssm_parameter(parameter_name: str, region: str) -> str:
    ssm = boto3.client("ssm", region_name=region)
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return response["Parameter"]["Value"]
