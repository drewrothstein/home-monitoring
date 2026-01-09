#!/usr/bin/env python3
"""
Helper script to obtain Enphase OAuth access token.

This script supports both single-app (legacy) and multi-app modes.

Multi-app mode: Use --app N to specify which app to configure (1, 2, 3, etc.)
Legacy mode: Omit --app to use the original single-app credentials

Usage:
    # Multi-app mode - Generate authorization URL for app 1:
    python scripts/get_enphase_token.py --app 1 --authorize-url

    # Multi-app mode - Exchange authorization code for app 1:
    python scripts/get_enphase_token.py --app 1 --exchange-code AUTHORIZATION_CODE

    # Multi-app mode - Refresh token for app 2:
    python scripts/get_enphase_token.py --app 2 --refresh-token REFRESH_TOKEN

    # Legacy mode (single-app):
    python scripts/get_enphase_token.py --authorize-url
    python scripts/get_enphase_token.py --exchange-code AUTHORIZATION_CODE

    # Partner apps (using username/password):
    python scripts/get_enphase_token.py --username YOUR_EMAIL --password YOUR_PASSWORD
"""

import argparse
import os
import sys

from dotenv import load_dotenv

# Add parent directory to path to import home_monitor modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file if it exists (for local development)
load_dotenv()

from home_monitor.apis.enphase import (  # noqa: E402
    exchange_authorization_code,
    get_access_token_from_password,
    get_authorization_url,
    refresh_access_token,
)


def get_app_credentials(app_index: int | None) -> tuple[str | None, str | None]:
    """
    Get credentials for a specific app or legacy mode.

    Args:
        app_index: App index (1, 2, 3, ...) or None for legacy mode

    Returns:
        Tuple of (client_id, client_secret)
    """
    if app_index is not None and app_index > 0:
        # Multi-app mode
        client_id = os.getenv(f"ENPHASE_APP_{app_index}_CLIENT_ID")
        client_secret = os.getenv(f"ENPHASE_APP_{app_index}_CLIENT_SECRET")
    else:
        # Legacy mode
        client_id = os.getenv("ENPHASE_CLIENT_ID")
        client_secret = os.getenv("ENPHASE_CLIENT_SECRET")

    return client_id, client_secret


def store_tokens_for_app(
    app_index: int | None, access_token: str, refresh_token: str, expires_in: int
) -> bool:
    """
    Store tokens for a specific app or legacy mode.

    Args:
        app_index: App index (1, 2, 3, ...) or None/0 for legacy mode
        access_token: OAuth access token
        refresh_token: OAuth refresh token
        expires_in: Token expiration in seconds

    Returns:
        True if stored successfully, False otherwise
    """
    from datetime import datetime, timedelta, timezone

    if app_index is not None and app_index > 0:
        # Multi-app mode - store in enphase_app_tokens table
        try:
            from home_monitor.database import update_enphase_app_tokens

            token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            update_enphase_app_tokens(
                app_index=app_index,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=token_expires_at,
            )
            return True
        except Exception as e:
            print(f"❌ ERROR storing tokens: {e}")
            return False
    else:
        # Legacy mode - store in location_api_configs
        try:
            from home_monitor.database import (
                get_any_enphase_config_with_tokens,
                get_location_api_config,
                get_locations,
            )
            from home_monitor.manage import add_api_config
            from home_monitor.token_manager import store_enphase_tokens_initial

            # Ensure at least one Enphase config exists
            if not get_any_enphase_config_with_tokens():
                has_any_config = False
                locations = get_locations()
                if locations:
                    for location in locations:
                        config = get_location_api_config(location["id"], "enphase")
                        if config:
                            has_any_config = True
                            break
                    if not has_any_config:
                        location_id = locations[0]["id"]
                        print(f"\nCreating Enphase API config for location_id={location_id}...")
                        add_api_config(location_id, "enphase", {})

            store_enphase_tokens_initial(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
            )
            return True
        except Exception as e:
            print(f"❌ ERROR storing tokens: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Obtain or refresh Enphase OAuth tokens (supports multiple apps)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--app",
        type=int,
        help="App index for multi-app mode (1, 2, 3, ...). Omit for legacy single-app mode.",
    )
    parser.add_argument(
        "--client-id",
        help="Client ID (overrides env var). Use ENPHASE_APP_N_CLIENT_ID for multi-app mode.",
    )
    parser.add_argument(
        "--client-secret",
        help="Client Secret (overrides env var). Use ENPHASE_APP_N_CLIENT_SECRET for multi-app mode.",
    )
    parser.add_argument(
        "--username",
        help="Enlighten email address (for Partner app method)",
    )
    parser.add_argument(
        "--password",
        help="Enlighten password (for Partner app method)",
    )
    parser.add_argument(
        "--authorize-url",
        action="store_true",
        help="Generate authorization URL (for Developer app OAuth flow)",
    )
    parser.add_argument(
        "--redirect-uri",
        help="Redirect URI for OAuth flow (default: Enphase default redirect)",
    )
    parser.add_argument(
        "--location-id",
        type=int,
        help="Location ID for legacy mode config creation (optional)",
    )
    parser.add_argument(
        "--exchange-code",
        help="Authorization code to exchange for tokens (from OAuth redirect)",
    )
    parser.add_argument(
        "--refresh-token",
        help="Refresh token to get new access token",
    )
    parser.add_argument(
        "--list-apps",
        action="store_true",
        help="List all configured Enphase apps and their status",
    )

    args = parser.parse_args()

    # List apps mode
    if args.list_apps:
        print("\n" + "=" * 70)
        print("📋 CONFIGURED ENPHASE APPS")
        print("=" * 70)

        # Check for multi-app configs
        import re

        pattern = re.compile(r"^ENPHASE_APP_(\d+)_API_KEY$")
        app_indices = []
        for key in os.environ:
            match = pattern.match(key)
            if match:
                app_indices.append(int(match.group(1)))

        if app_indices:
            print(f"\nFound {len(sorted(app_indices))} app(s) configured:\n")

            try:
                from home_monitor.database import get_all_enphase_app_stats

                stats = {s["app_index"]: s for s in get_all_enphase_app_stats()}
            except Exception:
                stats = {}

            for idx in sorted(app_indices):
                client_id = os.getenv(f"ENPHASE_APP_{idx}_CLIENT_ID", "")
                has_secret = bool(os.getenv(f"ENPHASE_APP_{idx}_CLIENT_SECRET"))
                has_key = bool(os.getenv(f"ENPHASE_APP_{idx}_API_KEY"))

                app_stats = stats.get(idx, {})
                has_token = app_stats.get("has_token", False)
                api_calls = app_stats.get("api_calls_today", 0)

                status = "✅" if has_token else "⚠️  No token"
                print(f"  App {idx}:")
                print(
                    f"    Client ID: {client_id[:20]}..."
                    if client_id
                    else "    Client ID: ❌ Missing"
                )
                print(f"    Client Secret: {'✅' if has_secret else '❌ Missing'}")
                print(f"    API Key: {'✅' if has_key else '❌ Missing'}")
                print(f"    Token: {status}")
                print(f"    API calls today: {api_calls}")
                print()
        else:
            print("\nNo multi-app configs found (ENPHASE_APP_N_* env vars).")

        # Check for legacy config
        legacy_client_id = os.getenv("ENPHASE_CLIENT_ID")
        if legacy_client_id:
            print("\nLegacy single-app config:")
            print(f"  Client ID: {legacy_client_id[:20]}...")
            print(
                f"  Client Secret: {'✅' if os.getenv('ENPHASE_CLIENT_SECRET') else '❌ Missing'}"
            )
            print(f"  API Key: {'✅' if os.getenv('ENPHASE_API_KEY') else '❌ Missing'}")

        print("\n" + "=" * 70 + "\n")
        return

    # Get credentials (from args or env)
    if args.client_id:
        client_id = args.client_id
    else:
        client_id, _ = get_app_credentials(args.app)

    if args.client_secret:
        client_secret = args.client_secret
    else:
        _, client_secret = get_app_credentials(args.app)

    # Validate client_id
    if not client_id and not args.list_apps:
        mode_hint = f"ENPHASE_APP_{args.app}_CLIENT_ID" if args.app else "ENPHASE_CLIENT_ID"
        print("❌ ERROR: Client ID is required")
        print("")
        print("Set it in your .env file:")
        print(f"  {mode_hint}=your_client_id")
        print("")
        print("Or pass it as an argument:")
        print("  --client-id your_client_id")
        print("")
        print("Get your Client ID from https://developer-v4.enphase.com/")
        sys.exit(1)

    app_label = f"App {args.app}" if args.app else "Legacy"

    # Generate authorization URL (Developer app method)
    if args.authorize_url:
        auth_url = get_authorization_url(client_id, redirect_uri=args.redirect_uri)
        print("\n" + "=" * 70)
        print(f"📋 AUTHORIZATION URL ({app_label})")
        print("=" * 70)
        print("\n1. Visit this URL in your browser:")
        print(f"\n   {auth_url}\n")
        print("2. Log in with your Enlighten credentials")
        print("3. Authorize the application")
        print("4. Copy the authorization code from the redirect URL")
        if args.app:
            print(f"5. Run: make enphase-exchange APP={args.app} CODE=<code>\n")
        else:
            print("5. Run: make enphase-exchange CODE=<code>\n")
        print("=" * 70 + "\n")
        return

    # Exchange authorization code (Developer app method)
    if args.exchange_code:
        if not client_secret:
            mode_hint = (
                f"ENPHASE_APP_{args.app}_CLIENT_SECRET" if args.app else "ENPHASE_CLIENT_SECRET"
            )
            print("❌ ERROR: Client Secret is required")
            print("")
            print("Set it in your .env file:")
            print(f"  {mode_hint}=your_client_secret")
            print("")
            print("Or pass it as an argument:")
            print("  --client-secret your_client_secret")
            sys.exit(1)

        try:
            token_data = exchange_authorization_code(
                client_id, client_secret, args.exchange_code, redirect_uri=args.redirect_uri
            )
            print("\n" + "=" * 70)
            print(f"✅ TOKEN EXCHANGE SUCCESSFUL ({app_label})")
            print("=" * 70)

            expires_in = token_data.get("expires_in", 86400)

            # Store tokens
            success = store_tokens_for_app(
                args.app,
                token_data["access_token"],
                token_data.get("refresh_token", ""),
                expires_in,
            )

            if success:
                if args.app:
                    print(f"\n✅ Tokens stored for App {args.app}")
                else:
                    print("\n✅ Tokens stored globally (legacy mode)")
            else:
                print("\n❌ Failed to store tokens in database")
                sys.exit(1)

            if isinstance(expires_in, int):
                hours = expires_in // 3600
                print(f"\nToken expires in: {expires_in} seconds ({hours} hours)")
            else:
                print(f"\nToken expires in: {expires_in} seconds")
            print("\n" + "=" * 70 + "\n")

        except Exception as e:
            print(f"❌ ERROR: Failed to exchange authorization code: {e}")
            sys.exit(1)
        return

    # Get token from password (Partner app method)
    if args.username and args.password:
        if not client_secret:
            print("❌ ERROR: Client Secret is required")
            print("Set it in your .env file or pass --client-secret")
            sys.exit(1)

        try:
            token_data = get_access_token_from_password(
                client_id, client_secret, args.username, args.password
            )
            print("\n" + "=" * 70)
            print(f"✅ TOKEN OBTAINED SUCCESSFULLY ({app_label})")
            print("=" * 70)

            expires_in = token_data.get("expires_in", 86400)

            # Store tokens
            success = store_tokens_for_app(
                args.app,
                token_data["access_token"],
                token_data.get("refresh_token", ""),
                expires_in,
            )

            if success:
                print(f"\n✅ Tokens stored for {app_label}")

            print(f"\nToken expires in: {expires_in} seconds")
            print("\n" + "=" * 70 + "\n")
        except Exception as e:
            print(f"❌ ERROR: Failed to get token: {e}")
            sys.exit(1)
        return

    # Refresh token
    if args.refresh_token:
        if not client_secret:
            print("❌ ERROR: Client Secret is required")
            print("Set it in your .env file or pass --client-secret")
            sys.exit(1)

        try:
            token_data = refresh_access_token(client_id, client_secret, args.refresh_token)
            print("\n" + "=" * 70)
            print(f"✅ TOKEN REFRESHED SUCCESSFULLY ({app_label})")
            print("=" * 70)

            expires_in = token_data.get("expires_in", 86400)

            # Store tokens
            success = store_tokens_for_app(
                args.app,
                token_data["access_token"],
                token_data.get("refresh_token", args.refresh_token),
                expires_in,
            )

            if success:
                print(f"\n✅ Tokens stored for {app_label}")
            else:
                print("\n⚠️  WARNING: Could not store tokens in database")

            if isinstance(expires_in, int):
                hours = expires_in // 3600
                print(f"\nToken expires in: {expires_in} seconds ({hours} hours)")
            else:
                print(f"\nToken expires in: {expires_in} seconds")
            print("\n" + "=" * 70 + "\n")
        except Exception as e:
            print(f"❌ ERROR: Failed to refresh token: {e}")
            sys.exit(1)
        return

    # No action specified
    parser.print_help()
    print("\n❌ ERROR: No action specified. Use one of:")
    print("  --list-apps (to see configured apps)")
    print("  --authorize-url (to generate OAuth URL)")
    print("  --exchange-code <code> (to exchange authorization code)")
    print("  --username <email> --password <pass> (Partner app method)")
    print("  --refresh-token <token> (to refresh access token)")
    print("")
    print("For multi-app mode, add --app N (e.g., --app 1)")
    sys.exit(1)


if __name__ == "__main__":
    main()
