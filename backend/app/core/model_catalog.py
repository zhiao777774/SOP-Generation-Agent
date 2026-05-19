import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from backend.app.core.config import ProviderConfig


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    provider: str
    context_window: Optional[int] = None


class ModelCatalog:
    def __init__(self, path: Path):
        self.path = path
        self._raw = self._load_raw(path)

    def list_models(self) -> List[CatalogModel]:
        models: List[CatalogModel] = []
        for provider_name, provider in self._raw.get("providers", {}).items():
            for model in provider.get("models", []):
                models.append(
                    CatalogModel(
                        id=model["id"],
                        name=model.get("name", model["id"]),
                        provider=provider_name,
                        context_window=model.get("contextWindow") or model.get("context_window"),
                    )
                )
        return models

    def resolve_llm(self, model_id: Optional[str]) -> ProviderConfig:
        providers: Dict = self._raw.get("providers", {})
        first_config: Optional[ProviderConfig] = None
        for provider_name, provider in providers.items():
            provider_models = provider.get("models", [])
            for model in provider_models:
                config = self._provider_config(provider, model)
                if first_config is None:
                    first_config = config
                if model_id and model["id"] == model_id:
                    return config
        if model_id:
            raise ValueError(f"Unknown model_id in models catalog: {model_id}")
        if first_config:
            return first_config
        raise ValueError(f"No models configured in {self.path}")

    def _provider_config(self, provider: Dict, model: Dict) -> ProviderConfig:
        api_key = provider.get("apiKey")
        api_key_env = provider.get("apiKeyEnv")
        if api_key_env:
            api_key = os.getenv(api_key_env, api_key)
        return ProviderConfig(
            api_base=provider.get("baseUrl"),
            api_key=api_key,
            model=model["id"],
            timeout_seconds=_timeout_seconds(provider, model),
        )

    def _load_raw(self, path: Path) -> Dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"providers": {}}


def _timeout_seconds(provider: Dict, model: Dict) -> float:
    value = (
        os.getenv("SOP_LLM_TIMEOUT_SECONDS")
        or model.get("timeoutSeconds")
        or model.get("timeout_seconds")
        or provider.get("timeoutSeconds")
        or provider.get("timeout_seconds")
        or 120
    )
    try:
        return float(value)
    except (TypeError, ValueError):
        return 120.0


def catalog_model_to_dict(model: CatalogModel) -> Dict:
    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider,
        "contextWindow": model.context_window,
    }
