"""
Configuration management using environment variables and dotenv.

Credentials are loaded from environment variables via a .env file.
"""

import logging
import os
from typing import Optional, Tuple

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env file if it exists (for local development)
load_dotenv()


def get_tesla_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Teslemetry API credentials from environment variables.

    Returns:
        Tuple of (api_key, None) - energy_site_id comes from sites.json config
    """
    api_key = os.getenv("TESLEMETRY_API_KEY")
    return (api_key, None) if api_key else (None, None)


def get_enphase_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Enphase API credentials from database and environment variables.

    This function provides backward compatibility with legacy single-app mode.
    For multi-app mode, use the EnphaseAppRotator from enphase_app_manager.

    Legacy mode:
    - Reads access_token from location_api_configs.config JSONB field (global, not location-specific)
    - API key comes from environment variables (ENPHASE_API_KEY)

    Multi-app mode:
    - Uses the first configured app's credentials
    - Tokens are stored in enphase_app_tokens table

    Returns:
        Tuple of (access_token, api_key)
    """
    # Check for multi-app mode first
    try:
        from home_monitor.enphase_app_manager import get_all_enphase_apps, load_app_with_tokens

        apps = get_all_enphase_apps()
        if apps and apps[0].app_index > 0:  # Multi-app mode (app_index > 0)
            app = load_app_with_tokens(apps[0])
            return (app.access_token, app.api_key)
    except Exception as e:
        logger.debug(f"Multi-app mode not available: {e}")

    # Fall back to legacy single-app mode
    access_token = None

    # Get access token from database (tokens are global, not location-specific)
    try:
        from home_monitor.database import get_any_enphase_config_with_tokens

        api_config = get_any_enphase_config_with_tokens()
        if api_config and api_config.get("config"):
            config = api_config["config"]
            if isinstance(config, dict):
                access_token = config.get("access_token")
    except Exception as e:
        logger.warning(f"Failed to get Enphase tokens from database: {e}")

    # API key always comes from environment variables (not stored in DB)
    api_key = os.getenv("ENPHASE_API_KEY")

    return (access_token, api_key)


def get_enphase_oauth_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Enphase OAuth credentials (Client ID and Client Secret) from environment variables.

    These are used for token refresh and OAuth flows.

    Returns:
        Tuple of (client_id, client_secret)
    """
    client_id = os.getenv("ENPHASE_CLIENT_ID")
    client_secret = os.getenv("ENPHASE_CLIENT_SECRET")

    return (client_id, client_secret)


def get_flume_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Flume API credentials from database and environment variables.

    - Reads access_token from location_api_configs.config JSONB field (global, not location-specific)
    - User ID is extracted from the JWT token

    Returns:
        Tuple of (access_token, user_id)
    """
    access_token = None
    user_id = None

    # Get access token and user_id from database (tokens are global, not location-specific)
    try:
        from home_monitor.database import get_any_flume_config_with_tokens

        api_config = get_any_flume_config_with_tokens()
        if api_config and api_config.get("config"):
            config = api_config["config"]
            if isinstance(config, dict):
                access_token = config.get("access_token")
                user_id = config.get("user_id")
    except Exception as e:
        logger.warning(f"Failed to get Flume tokens from database: {e}")

    return (access_token, user_id)


def get_flume_oauth_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Flume OAuth credentials (Client ID and Client Secret) from environment variables.

    These are used for token refresh and OAuth flows.

    Returns:
        Tuple of (client_id, client_secret)
    """
    client_id = os.getenv("FLUME_CLIENT_ID")
    client_secret = os.getenv("FLUME_CLIENT_SECRET")

    return (client_id, client_secret)


def get_flume_username() -> Optional[str]:
    """
    Get Flume account username from environment variables.

    Used for the initial OAuth password grant flow (password is prompted interactively).

    Returns:
        Username string or None
    """
    return os.getenv("FLUME_USERNAME")


def _get_location_credential(
    base_env_var: str, location_name: Optional[str] = None
) -> Optional[str]:
    """Helper to get location-specific or global credential from environment."""
    if location_name:
        location_key = location_name.upper().replace(" ", "_").replace("-", "_")
        credential = os.getenv(f"{base_env_var}_{location_key}")
        if credential:
            return credential
    return os.getenv(base_env_var)


def get_openweather_api_key(location_name: Optional[str] = None) -> Optional[str]:
    """
    Get OpenWeather API key from environment variables.

    Supports both global and location-specific keys:
    - Global: OPENWEATHER_API_KEY
    - Location-specific: OPENWEATHER_API_KEY_{LOCATION_NAME}

    Args:
        location_name: Optional location name for location-specific key

    Returns:
        API key string or None
    """
    return _get_location_credential("OPENWEATHER_API_KEY", location_name)


def get_tempest_credentials(location_name: Optional[str] = None) -> Optional[str]:
    """
    Get Tempest API token from environment variables.

    Supports both global and location-specific tokens:
    - Global: TEMPEST_TOKEN
    - Location-specific: TEMPEST_TOKEN_{LOCATION_NAME}

    Args:
        location_name: Optional location name for location-specific token

    Returns:
        Token string or None
    """
    return _get_location_credential("TEMPEST_TOKEN", location_name)


def get_rachio_credentials() -> Optional[str]:
    """
    Get Rachio API key from environment variables.

    The API key can be obtained from https://app.rach.io/ under Account Settings.

    Returns:
        API key string or None
    """
    return os.getenv("RACHIO_API_KEY")


def get_tankutility_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Tank Utility credentials from environment variables.

    These are the email/password for your Tank Utility account at
    https://app.tankutility.com/

    Returns:
        Tuple of (email, password)
    """
    email = os.getenv("TANK_UTILITY_EMAIL")
    password = os.getenv("TANK_UTILITY_PASSWORD")
    return (email, password)


def get_iaqualink_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get iAqualink credentials from environment variables.

    These are the email/password for your iAqualink account at
    https://www.iaqualink.com/

    Returns:
        Tuple of (email, password)
    """
    email = os.getenv("IAQUALINK_EMAIL")
    password = os.getenv("IAQUALINK_PASSWORD")
    return (email, password)


def get_enphase_fetch_interval_cycles() -> int:
    """
    Get the number of fetch cycles between Enphase API calls.

    Enphase Free plan has limited API calls (1k/month), so we fetch less frequently.
    Default is 3 cycles (every 15 minutes when using 5-minute fetch intervals).

    Environment variable: ENPHASE_FETCH_INTERVAL_CYCLES

    Returns:
        Number of cycles between Enphase fetches (default: 3)
    """
    try:
        return int(os.getenv("ENPHASE_FETCH_INTERVAL_CYCLES", "3"))
    except ValueError:
        return 3
