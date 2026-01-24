"""
Manages multiple Enphase API apps for rate limit distribution.

Supports N apps with:
- Round-robin rotation across apps
- Automatic failover if an app fails (rate limit, auth error)
- Per-app request tracking
- Automatic token refresh per app
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Note: Round-robin rotation removed in favor of "primary first" logic.
# App #1 is always tried first; higher-numbered apps are fallbacks for rate limits.


@dataclass
class EnphaseAppConfig:
    """Configuration for a single Enphase app."""

    app_index: int  # 1-based index, or 0 for legacy single-app mode
    api_key: str
    client_id: str
    client_secret: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    # Tracking
    api_calls_today: int = 0
    last_reset_date: Optional[str] = None
    # Runtime state (not persisted)
    _failed_this_cycle: bool = field(default=False, repr=False)


def _discover_enphase_app_indices() -> List[int]:
    """
    Discover all configured Enphase app indices by scanning environment variables.

    Looks for ENPHASE_APP_{N}_API_KEY patterns and returns sorted list of N values.
    Only includes apps that have all required credentials configured.

    Returns:
        Sorted list of app indices (e.g., [1, 2, 3])
    """
    pattern = re.compile(r"^ENPHASE_APP_(\d+)_API_KEY$")
    indices = []

    for key in os.environ:
        match = pattern.match(key)
        if match:
            index = int(match.group(1))
            # Verify all required credentials exist for this app
            if all(
                [
                    os.getenv(f"ENPHASE_APP_{index}_API_KEY"),
                    os.getenv(f"ENPHASE_APP_{index}_CLIENT_ID"),
                    os.getenv(f"ENPHASE_APP_{index}_CLIENT_SECRET"),
                ]
            ):
                indices.append(index)

    return sorted(indices)


def get_enphase_app_credentials(app_index: int) -> Optional[EnphaseAppConfig]:
    """
    Get credentials for a specific Enphase app from environment variables.

    Args:
        app_index: App index (1, 2, 3, ... N)

    Returns:
        EnphaseAppConfig or None if not fully configured
    """
    api_key = os.getenv(f"ENPHASE_APP_{app_index}_API_KEY")
    client_id = os.getenv(f"ENPHASE_APP_{app_index}_CLIENT_ID")
    client_secret = os.getenv(f"ENPHASE_APP_{app_index}_CLIENT_SECRET")

    if not all([api_key, client_id, client_secret]):
        return None

    return EnphaseAppConfig(
        app_index=app_index,
        api_key=api_key,
        client_id=client_id,
        client_secret=client_secret,
    )


def get_legacy_enphase_credentials() -> Optional[EnphaseAppConfig]:
    """
    Get legacy single-app Enphase credentials (backward compatibility).

    Uses the original ENPHASE_API_KEY, ENPHASE_CLIENT_ID, ENPHASE_CLIENT_SECRET
    environment variables.

    Returns:
        EnphaseAppConfig with app_index=0 for legacy mode, or None
    """
    api_key = os.getenv("ENPHASE_API_KEY")
    client_id = os.getenv("ENPHASE_CLIENT_ID")
    client_secret = os.getenv("ENPHASE_CLIENT_SECRET")

    if not all([api_key, client_id, client_secret]):
        return None

    return EnphaseAppConfig(
        app_index=0,  # 0 = legacy single-app mode
        api_key=api_key,
        client_id=client_id,
        client_secret=client_secret,
    )


def get_all_enphase_apps() -> List[EnphaseAppConfig]:
    """
    Get all configured Enphase apps with their credentials.

    First checks for multi-app config (ENPHASE_APP_N_*),
    falls back to legacy single-app config if none found.

    Returns:
        List of EnphaseAppConfig (may be empty if nothing configured)
    """
    # Try multi-app mode first
    indices = _discover_enphase_app_indices()
    if indices:
        apps = []
        for idx in indices:
            app = get_enphase_app_credentials(idx)
            if app:
                apps.append(app)
        if apps:
            logger.debug(f"[Enphase] Found {len(apps)} multi-app configs: {indices}")
            return apps

    # Fall back to legacy single-app mode
    legacy = get_legacy_enphase_credentials()
    if legacy:
        logger.debug("[Enphase] Using legacy single-app config")
        return [legacy]

    return []


def get_enphase_app_count() -> int:
    """Get total number of configured Enphase apps."""
    return len(get_all_enphase_apps())


def load_app_with_tokens(app: EnphaseAppConfig) -> EnphaseAppConfig:
    """
    Load tokens from database for an app.

    Args:
        app: App config to load tokens for

    Returns:
        App config with tokens populated
    """
    from home_monitor.database import get_enphase_app_tokens

    tokens = get_enphase_app_tokens(app.app_index)
    if tokens:
        app.access_token = tokens.get("access_token")
        app.refresh_token = tokens.get("refresh_token")
        expires_at = tokens.get("token_expires_at")
        if expires_at:
            if isinstance(expires_at, str):
                try:
                    app.token_expires_at = datetime.fromisoformat(expires_at)
                except ValueError:
                    pass
            elif isinstance(expires_at, datetime):
                app.token_expires_at = expires_at
        app.api_calls_today = tokens.get("api_calls_today", 0)
        app.last_reset_date = tokens.get("last_reset_date")

    return app


def refresh_app_token_if_needed(app: EnphaseAppConfig) -> Tuple[Optional[str], bool]:
    """
    Refresh token for a specific app if expired or expiring soon.

    Args:
        app: The app config to check/refresh

    Returns:
        Tuple of (access_token, was_refreshed)
    """
    from home_monitor.apis.enphase import refresh_access_token
    from home_monitor.database import update_enphase_app_tokens

    if not app.access_token:
        logger.warning(f"[Enphase] App {app.app_index} has no access token")
        return None, False

    # Check if token expires soon (within 1 hour)
    needs_refresh = False
    if app.token_expires_at:
        expires_at = app.token_expires_at
        # Ensure timezone aware
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) + timedelta(hours=1) >= expires_at:
            needs_refresh = True
            logger.info(f"[Enphase] App {app.app_index} token expiring soon, refreshing...")
    elif app.refresh_token:
        # No expiration stored but we have refresh token - try refreshing
        logger.info(f"[Enphase] App {app.app_index} no expiration stored, attempting refresh")
        needs_refresh = True

    if needs_refresh and app.refresh_token:
        try:
            token_data = refresh_access_token(app.client_id, app.client_secret, app.refresh_token)

            new_access_token = token_data.get("access_token")
            new_refresh_token = token_data.get("refresh_token", app.refresh_token)
            expires_in = token_data.get("expires_in", 86400)

            if new_access_token:
                expiration_time = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

                # Update in database
                update_enphase_app_tokens(
                    app_index=app.app_index,
                    access_token=new_access_token,
                    refresh_token=new_refresh_token,
                    token_expires_at=expiration_time,
                )

                # Update in-memory
                app.access_token = new_access_token
                app.refresh_token = new_refresh_token
                app.token_expires_at = expiration_time

                logger.info(f"[Enphase] Successfully refreshed token for app {app.app_index}")
                return new_access_token, True
            else:
                logger.error(f"[Enphase] App {app.app_index} refresh succeeded but no access_token")
                return app.access_token, False

        except Exception as e:
            logger.error(f"[Enphase] Failed to refresh token for app {app.app_index}: {e}")
            if app.access_token:
                logger.info(
                    f"[Enphase] App {app.app_index} will continue with existing token "
                    f"(may still be valid)"
                )
            return app.access_token, False

    return app.access_token, False


def increment_app_api_calls(app_index: int) -> None:
    """
    Increment the API call counter for an app.

    Automatically resets the counter if it's a new day.

    Args:
        app_index: The app index to increment
    """
    from home_monitor.database import increment_enphase_app_api_calls

    increment_enphase_app_api_calls(app_index)


def get_next_app_with_failover(
    exclude_indices: Optional[List[int]] = None,
) -> Optional[EnphaseAppConfig]:
    """
    Get the next available Enphase app, excluding failed ones.

    Uses "primary first" logic: always tries the lowest-indexed app first.
    Higher-numbered apps are only used as fallbacks when lower ones fail
    (e.g., due to rate limits).

    Args:
        exclude_indices: List of app indices to skip (failed apps)

    Returns:
        Next available app (lowest index not excluded), or None if all exhausted
    """
    apps = get_all_enphase_apps()
    if not apps:
        return None

    exclude_indices = exclude_indices or []
    available_apps = [a for a in apps if a.app_index not in exclude_indices]

    if not available_apps:
        logger.warning("[Enphase] All apps have been tried and failed")
        return None

    # Primary-first: always use the lowest-indexed available app
    # (apps are already sorted by index from get_all_enphase_apps)
    app = available_apps[0]

    # Load tokens
    app = load_app_with_tokens(app)

    logger.debug(
        f"[Enphase] Selected app {app.app_index} "
        f"({len(available_apps)} available, {len(exclude_indices)} excluded)"
    )

    return app


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an error is a rate limit error."""
    error_str = str(error).lower()
    return any(
        x in error_str
        for x in [
            "429",
            "rate limit",
            "too many requests",
            "quota exceeded",
            "limit exceeded",
        ]
    )


def is_auth_error(error: Exception) -> bool:
    """Check if an error is an authentication error."""
    error_str = str(error).lower()
    return any(x in error_str for x in ["401", "403", "unauthorized", "forbidden", "token"])


class EnphaseAppRotator:
    """
    Context manager for Enphase API calls with automatic failover.

    Uses "primary first" logic: always tries app #1 first, then falls back
    to #2, #3, etc. only if the previous app fails (e.g., rate limit 429).

    Usage:
        rotator = EnphaseAppRotator()
        while rotator.has_more_apps():
            app = rotator.get_current_app()
            try:
                # Make API call with app credentials
                result = make_api_call(app.access_token, app.api_key)
                rotator.mark_success()
                break
            except Exception as e:
                rotator.mark_failure(e)
                if not rotator.should_retry(e):
                    raise
    """

    def __init__(self):
        self.failed_indices: List[int] = []
        self.current_app: Optional[EnphaseAppConfig] = None
        self.success = False

    def has_more_apps(self) -> bool:
        """Check if there are more apps to try."""
        if self.success:
            return False
        apps = get_all_enphase_apps()
        return len(self.failed_indices) < len(apps)

    def get_current_app(self) -> Optional[EnphaseAppConfig]:
        """Get the current app to try (with failover)."""
        self.current_app = get_next_app_with_failover(exclude_indices=self.failed_indices)
        if self.current_app:
            # Refresh token if needed
            access_token, _ = refresh_app_token_if_needed(self.current_app)
            self.current_app.access_token = access_token
        return self.current_app

    def mark_success(self) -> None:
        """Mark the current app call as successful."""
        self.success = True
        if self.current_app:
            increment_app_api_calls(self.current_app.app_index)
            if self.failed_indices:
                logger.info(
                    f"[Enphase] App {self.current_app.app_index} succeeded after "
                    f"{len(self.failed_indices)} failed app(s): {self.failed_indices}"
                )
            else:
                logger.debug(f"[Enphase] App {self.current_app.app_index} call successful")

    def mark_failure(self, error: Exception) -> None:
        """Mark the current app as failed."""
        if self.current_app:
            self.failed_indices.append(self.current_app.app_index)
            remaining = self._count_remaining_apps()
            if remaining > 0:
                logger.warning(
                    f"[Enphase] App {self.current_app.app_index} failed: {error}. "
                    f"Will try next app ({remaining} remaining)"
                )
            else:
                logger.error(
                    f"[Enphase] App {self.current_app.app_index} failed: {error}. "
                    f"No more apps to try (all {len(self.failed_indices)} exhausted)"
                )

    def _count_remaining_apps(self) -> int:
        """Count how many apps are still available to try."""
        apps = get_all_enphase_apps()
        return len(apps) - len(self.failed_indices)

    def should_retry(self, error: Exception) -> bool:
        """Check if we should retry with another app."""
        # Retry on rate limit or auth errors if we have more apps
        if is_rate_limit_error(error) or is_auth_error(error):
            return self.has_more_apps()
        return False
