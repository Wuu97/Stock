"""Load versioned cost models without a runtime YAML dependency.

The repository file uses JSON syntax, which is also valid YAML, preserving a
human-editable configuration while keeping Python 3.9 installs minimal.
"""

from decimal import Decimal
import json
from pathlib import Path

from .models import FeeModel


def load_cost_model(version: str, config_path: Path) -> FeeModel:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        values = payload["cost_models"][version]
    except KeyError as error:
        raise ValueError(f"cost model version is not configured: {version}") from error
    return FeeModel(version, Decimal(values["commission_rate"]), Decimal(values["min_commission"]),
                    Decimal(values["stamp_duty_rate"]), Decimal(values["transfer_fee_rate"]),
                    Decimal(values["default_slippage_rate"]))
