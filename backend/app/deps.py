from .config import get_settings
from .services.hub import HubClient


def get_hub() -> HubClient:
    return HubClient(get_settings())
