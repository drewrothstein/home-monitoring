"""
Token management for OAuth tokens stored in the database.

Handles automatic token refresh when tokens expire.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from home_monitor.apis.enphase import refresh_access_token as enphase_refresh_access_token
from home_monitor.apis.flume import refresh_access_token as flume_refresh_access_token
from home_monitor.config import get_enphase_oauth_credentials, get_flume_oauth_credentials
from home_monitor.database import (
    get_any_enphase_config_with_tokens,
    get_any_flume_config_with_tokens,
    update_enphase_tokens_globally,
    update_flume_tokens_globally,
)

logger = logging.getLogger(__name__)


def refresh_enphase_token_if_needed() -> Tuple[Optional[str], bool]:
    """
    Refresh Enphase access token if it's expired or about to expire.

    Since Enphase tokens are account-level (not location-specific), we check/refresh
    tokens globally. Checks if the token is expired or will expire soon (within 1 hour),
    and if so, refreshes it using the refresh token stored in the database.

    Returns:
        Tuple of (access_token, was_refreshed)
        - access_token: The current (or newly refreshed) access token
        - was_refreshed: True if token was refreshed, False otherwise
    """
    try:
        # Get any Enphase API config with tokens (tokens are global, not location-specific)
        api_config = get_any_enphase_config_with_tokens()
        if not api_config or not api_config.get("config"):
            logger.warning("No Enphase API config with tokens found in database")
            return None, False

        config = api_config["config"]
        if isinstance(config, dict):
            access_token = config.get("access_token")
            refresh_token = config.get("refresh_token")
            token_expires_at_str = config.get("token_expires_at")

            if not access_token:
                logger.warning("No access token found in database")
                return None, False

            # Check if token is expired or will expire soon (within 1 hour)
            needs_refresh = False
            if token_expires_at_str:
                try:
                    token_expires_at = datetime.fromisoformat(token_expires_at_str)
                    # Refresh if expired or will expire within 1 hour
                    if datetime.now(timezone.utc) + timedelta(hours=1) >= token_expires_at:
                        needs_refresh = True
                        logger.info("Enphase token expires soon or is expired, refreshing...")
                except (ValueError, TypeError):
                    # If we can't parse expiration, try to refresh anyway if we have a refresh token
                    if refresh_token:
                        logger.warning("Could not parse token expiration, attempting refresh")
                        needs_refresh = True
            elif refresh_token:
                # No expiration time stored, but we have a refresh token - try refreshing
                logger.info("No expiration time stored, attempting refresh to get expiration")
                needs_refresh = True

            if needs_refresh and refresh_token:
                # Get OAuth credentials from environment
                client_id, client_secret = get_enphase_oauth_credentials()
                if not client_id or not client_secret:
                    logger.error(
                        "Cannot refresh token: ENPHASE_CLIENT_ID and ENPHASE_CLIENT_SECRET required in environment"
                    )
                    return access_token, False

                try:
                    # Refresh the token
                    token_data = enphase_refresh_access_token(
                        client_id, client_secret, refresh_token
                    )

                    new_access_token = token_data.get("access_token")
                    new_refresh_token = token_data.get(
                        "refresh_token", refresh_token
                    )  # Keep old if not provided
                    expires_in = token_data.get("expires_in", 86400)  # Default 24 hours

                    if new_access_token:
                        # Calculate expiration time
                        expiration_time = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

                        # Update tokens globally in all Enphase configs
                        update_enphase_tokens_globally(
                            access_token=new_access_token,
                            refresh_token=new_refresh_token,
                            token_expires_at=expiration_time,
                        )

                        logger.info("Successfully refreshed Enphase token (updated globally)")
                        return new_access_token, True
                    else:
                        logger.error("Token refresh succeeded but no access_token in response")
                        return access_token, False

                except Exception as e:
                    logger.error(f"Failed to refresh Enphase token: {e}", exc_info=True)
                    # Return existing token even though it may be expired
                    return access_token, False

            # Token is still valid
            return access_token, False

    except Exception as e:
        logger.error(f"Error checking/refreshing Enphase token: {e}", exc_info=True)
        return None, False

    return None, False


def store_enphase_tokens_initial(
    access_token: str,
    refresh_token: str,
    expires_in: int = 86400,
) -> None:
    """
    Store initial Enphase tokens in the database after OAuth authorization.

    Since Enphase tokens are account-level (not location-specific), tokens are
    stored globally in all Enphase API configs.

    Args:
        access_token: Access token from OAuth flow
        refresh_token: Refresh token from OAuth flow
        expires_in: Token expiration time in seconds (default: 86400 = 24 hours)
    """
    expiration_time = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    update_enphase_tokens_globally(
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expiration_time,
    )
    logger.info("Stored initial Enphase tokens (updated globally)")


def refresh_flume_token_if_needed() -> Tuple[Optional[str], bool]:
    """
    Refresh Flume access token if it's expired or about to expire.

    Since Flume tokens are account-level (not location-specific), we check/refresh
    tokens globally. Checks if the token is expired or will expire soon (within 1 hour),
    and if so, refreshes it using the refresh token stored in the database.

    Returns:
        Tuple of (access_token, was_refreshed)
        - access_token: The current (or newly refreshed) access token
        - was_refreshed: True if token was refreshed, False otherwise
    """
    try:
        # Get any Flume API config with tokens (tokens are global, not location-specific)
        api_config = get_any_flume_config_with_tokens()
        if not api_config or not api_config.get("config"):
            logger.warning("No Flume API config with tokens found in database")
            return None, False

        config = api_config["config"]
        if isinstance(config, dict):
            access_token = config.get("access_token")
            refresh_token = config.get("refresh_token")
            token_expires_at_str = config.get("token_expires_at")
            user_id = config.get("user_id")

            if not access_token:
                logger.warning("No Flume access token found in database")
                return None, False

            # Check if token is expired or will expire soon (within 1 hour)
            needs_refresh = False
            if token_expires_at_str:
                try:
                    token_expires_at = datetime.fromisoformat(token_expires_at_str)
                    # Refresh if expired or will expire within 1 hour
                    if datetime.now(timezone.utc) + timedelta(hours=1) >= token_expires_at:
                        needs_refresh = True
                        logger.info("Flume token expires soon or is expired, refreshing...")
                except (ValueError, TypeError):
                    # If we can't parse expiration, try to refresh anyway if we have a refresh token
                    if refresh_token:
                        logger.warning("Could not parse Flume token expiration, attempting refresh")
                        needs_refresh = True
            elif refresh_token:
                # No expiration time stored, but we have a refresh token - try refreshing
                logger.info("No Flume expiration time stored, attempting refresh to get expiration")
                needs_refresh = True

            if needs_refresh and refresh_token:
                # Get OAuth credentials from environment
                client_id, client_secret = get_flume_oauth_credentials()
                if not client_id or not client_secret:
                    logger.error(
                        "Cannot refresh Flume token: FLUME_CLIENT_ID and FLUME_CLIENT_SECRET required in environment"
                    )
                    return access_token, False

                try:
                    # Refresh the token
                    token_data = flume_refresh_access_token(client_id, client_secret, refresh_token)

                    new_access_token = token_data.get("access_token")
                    new_refresh_token = token_data.get(
                        "refresh_token", refresh_token
                    )  # Keep old if not provided

                    if new_access_token:
                        # Flume tokens don't have an explicit expires_in, estimate 24 hours
                        expiration_time = datetime.now(timezone.utc) + timedelta(hours=24)

                        # Update tokens globally in all Flume configs
                        update_flume_tokens_globally(
                            access_token=new_access_token,
                            refresh_token=new_refresh_token,
                            token_expires_at=expiration_time,
                            user_id=user_id,
                        )

                        logger.info("Successfully refreshed Flume token (updated globally)")
                        return new_access_token, True
                    else:
                        logger.error(
                            "Flume token refresh succeeded but no access_token in response"
                        )
                        return access_token, False

                except Exception as e:
                    logger.error(f"Failed to refresh Flume token: {e}", exc_info=True)
                    # Return existing token even though it may be expired
                    return access_token, False

            # Token is still valid
            return access_token, False

    except Exception as e:
        logger.error(f"Error checking/refreshing Flume token: {e}", exc_info=True)
        return None, False

    return None, False


def store_flume_tokens_initial(
    access_token: str,
    refresh_token: str,
    user_id: Optional[str] = None,
    expires_in: int = 86400,
) -> None:
    """
    Store initial Flume tokens in the database after OAuth authorization.

    Since Flume tokens are account-level (not location-specific), tokens are
    stored globally in all Flume API configs.

    Args:
        access_token: Access token from OAuth flow
        refresh_token: Refresh token from OAuth flow
        user_id: User ID extracted from JWT (optional)
        expires_in: Token expiration time in seconds (default: 86400 = 24 hours)
    """
    expiration_time = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    update_flume_tokens_globally(
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expiration_time,
        user_id=user_id,
    )
    logger.info("Stored initial Flume tokens (updated globally)")
