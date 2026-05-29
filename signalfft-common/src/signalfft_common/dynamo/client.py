"""DynamoDB client wrapper for SignalFFT domain models.

Provides a thin layer over boto3's DynamoDB Table resource, with helpers for
putting/getting dataclass-based models and batch writes.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


class DynamoClient:
    """Convenience wrapper around a single DynamoDB table."""

    def __init__(self, table_name: str, region: str = "us-east-1"):
        self._resource = boto3.resource("dynamodb", region_name=region)
        self._table = self._resource.Table(table_name)
        self.table_name = table_name

    def put_item_from_model(self, model: Any, pk: str, sk: str) -> None:
        """Serialise a dataclass *model* and write it with the given PK/SK."""
        item = dataclasses.asdict(model)
        # Convert sets to lists for DynamoDB compatibility
        item = self._convert_sets(item)
        item["PK"] = pk
        item["SK"] = sk
        self._table.put_item(Item=item)

    def get_item_to_model(self, model_class: type, pk: str, sk: str) -> Any | None:
        """Fetch a single item and reconstruct it as *model_class*.

        Returns ``None`` when the item does not exist.
        """
        response = self._table.get_item(Key={"PK": pk, "SK": sk})
        item = response.get("Item")
        if item is None:
            return None
        # Remove PK/SK before constructing model
        item.pop("PK", None)
        item.pop("SK", None)
        return model_class(**item)

    def query_by_pk(
        self,
        pk: str,
        sk_prefix: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query items sharing a partition key, optionally filtering by SK prefix."""
        if sk_prefix:
            key_condition = Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix)
        else:
            key_condition = Key("PK").eq(pk)
        response = self._table.query(
            KeyConditionExpression=key_condition,
            Limit=limit,
        )
        return response.get("Items", [])

    def batch_write(self, items: list[dict]) -> None:
        """Batch-put a list of raw item dicts (must already contain PK/SK)."""
        with self._table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=self._convert_sets(item))

    @staticmethod
    def _convert_sets(item: dict) -> dict:
        """Recursively convert sets to sorted lists for DynamoDB."""
        converted: dict = {}
        for key, value in item.items():
            if isinstance(value, set):
                converted[key] = sorted(value)
            elif isinstance(value, dict):
                converted[key] = DynamoClient._convert_sets(value)
            elif isinstance(value, list):
                converted[key] = [
                    DynamoClient._convert_sets(v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                converted[key] = value
        return converted
