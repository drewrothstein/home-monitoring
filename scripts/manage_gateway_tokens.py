#!/usr/bin/env python3
"""
Manage Enphase IQ Gateway tokens for local API access.

This script allows you to:
- Store gateway tokens in the database
- List stored gateway tokens
- Test gateway connectivity
- Refresh tokens (requires Enlighten session)

Usage:
    # Store a new gateway token
    python scripts/manage_gateway_tokens.py store --serial 123456789012 --host 192.168.1.100 --token "your_token_here"

    # List all stored gateway tokens
    python scripts/manage_gateway_tokens.py list

    # Test connectivity to a gateway
    python scripts/manage_gateway_tokens.py test --serial 123456789012

    # Delete a gateway token
    python scripts/manage_gateway_tokens.py delete --serial 123456789012
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

# Add parent directory to path for imports
sys.path.insert(0, ".")

from home_monitor.database import (  # noqa: E402
    delete_enphase_gateway_token,
    get_all_enphase_gateway_tokens,
    get_enphase_gateway_token,
    init_database,
    upsert_enphase_gateway_token,
)


def store_token(args):
    """Store a gateway token in the database."""
    # Parse expiration - owner tokens are valid for 1 year
    if args.expires_at:
        try:
            expires_at = datetime.fromisoformat(args.expires_at)
        except ValueError:
            # Try parsing as Unix timestamp
            try:
                expires_at = datetime.fromtimestamp(int(args.expires_at), tz=timezone.utc)
            except ValueError:
                print(f"Error: Invalid expiration format: {args.expires_at}")
                print("Use ISO format (2025-01-01T00:00:00Z) or Unix timestamp")
                return 1
    else:
        # Default: 1 year from now (owner token duration)
        expires_at = datetime.now(timezone.utc) + timedelta(days=365)

    token_id = upsert_enphase_gateway_token(
        gateway_serial=args.serial,
        gateway_host=args.host,
        token=args.token,
        token_expires_at=expires_at,
        location_id=args.location_id,
    )

    print(f"✓ Stored token for gateway {args.serial}")
    print(f"  Host: {args.host}")
    print(f"  Expires: {expires_at.isoformat()}")
    print(f"  Database ID: {token_id}")
    return 0


def list_tokens(args):
    """List all stored gateway tokens."""
    tokens = get_all_enphase_gateway_tokens()

    if not tokens:
        print("No gateway tokens stored.")
        return 0

    print(f"\n{'Serial':<15} {'Host':<18} {'Expires':<25} {'Location ID':<12}")
    print("-" * 75)

    now = datetime.now(timezone.utc)
    for token in tokens:
        serial = token.get("gateway_serial", "?")
        host = token.get("gateway_host", "?")
        expires_at = token.get("token_expires_at")
        location_id = token.get("location_id") or "-"

        if expires_at:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            days_left = (expires_at - now).days
            if days_left < 0:
                expires_str = f"EXPIRED ({-days_left}d ago)"
            elif days_left < 30:
                expires_str = f"{expires_at.strftime('%Y-%m-%d')} ({days_left}d left) ⚠️"
            else:
                expires_str = f"{expires_at.strftime('%Y-%m-%d')} ({days_left}d left)"
        else:
            expires_str = "Unknown"

        print(f"{serial:<15} {host:<18} {expires_str:<25} {str(location_id):<12}")

    print()
    return 0


def test_gateway(args):
    """Test connectivity to a gateway."""
    from home_monitor.apis.enphase_local import EnphaseLocalClient

    # Get token from database
    token_data = get_enphase_gateway_token(args.serial)
    if not token_data:
        print(f"Error: No token found for gateway {args.serial}")
        print("Use 'store' command to add a token first.")
        return 1

    gateway_host = token_data.get("gateway_host")
    token = token_data.get("token")

    print(f"Testing connection to gateway {args.serial} at {gateway_host}...")

    client = EnphaseLocalClient(
        gateway_host=gateway_host,
        token=token,
        gateway_serial=args.serial,
    )

    try:
        # Test basic connectivity
        if client.check_connection():
            print("✓ Gateway connection successful")
        else:
            print("✗ Gateway connection failed")
            return 1

        # Get system info
        try:
            info = client.get_info()
            print(f"  Software: {info.get('software')}")
            print(f"  Device: {info.get('device')}")
            print(f"  Serial: {info.get('serial')}")
        except Exception as e:
            print(f"  Warning: Could not get system info: {e}")

        # Get current data
        try:
            data = client.fetch_current_data()
            print("\nCurrent readings:")
            print(f"  Power Produced: {data.get('power_produced')} W")
            print(f"  Power Consumed: {data.get('power_consumed')} W")
            print(f"  Power Net: {data.get('power_net')} W")
            print(f"  Grid Voltage L1: {data.get('grid_voltage_l1')} V")
            print(f"  Grid Voltage L2: {data.get('grid_voltage_l2')} V")
            print(f"  Grid Frequency: {data.get('grid_frequency')} Hz")
        except Exception as e:
            print(f"  Warning: Could not fetch current data: {e}")

        return 0

    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return 1


def delete_token(args):
    """Delete a gateway token."""
    if delete_enphase_gateway_token(args.serial):
        print(f"✓ Deleted token for gateway {args.serial}")
        return 0
    else:
        print(f"No token found for gateway {args.serial}")
        return 1


def refresh_token(args):
    """Refresh a gateway token using Enlighten credentials."""
    from home_monitor.apis.enphase_local import (
        EnlightenSession,
        get_enlighten_credentials,
        refresh_gateway_token,
    )

    # Check if credentials are available
    username, password = get_enlighten_credentials()
    if not username or not password:
        print("Error: Enlighten credentials not configured.")
        print("Set ENPHASE_ENLIGHTEN_USERNAME and ENPHASE_ENLIGHTEN_PASSWORD in your .env file.")
        return 1

    # If refreshing all gateways
    if args.all:
        tokens = get_all_enphase_gateway_tokens()
        if not tokens:
            print("No gateway tokens stored.")
            return 0

        print(f"Refreshing tokens for {len(tokens)} gateway(s)...\n")

        session = EnlightenSession(username, password)
        success_count = 0
        fail_count = 0

        for token_data in tokens:
            serial = token_data.get("gateway_serial")
            host = token_data.get("gateway_host")
            print(f"Refreshing {serial}...", end=" ")

            try:
                new_token_data = session.get_gateway_token(serial)
                if new_token_data:
                    upsert_enphase_gateway_token(
                        gateway_serial=serial,
                        gateway_host=host,
                        token=new_token_data["token"],
                        token_expires_at=new_token_data["expires_at"],
                    )
                    print(f"✓ Expires: {new_token_data['expires_at'].strftime('%Y-%m-%d')}")
                    success_count += 1
                else:
                    print("✗ Failed")
                    fail_count += 1
            except Exception as e:
                print(f"✗ {e}")
                fail_count += 1

        print(f"\nRefreshed {success_count} token(s), {fail_count} failed.")
        return 0 if fail_count == 0 else 1

    # Refresh specific gateway
    token_data = get_enphase_gateway_token(args.serial)
    if not token_data:
        print(f"Error: No token found for gateway {args.serial}")
        print("Use 'store' command to add a token first.")
        return 1

    gateway_host = token_data.get("gateway_host")

    print(f"Refreshing token for gateway {args.serial}...")

    new_token_data = refresh_gateway_token(args.serial, gateway_host)

    if new_token_data:
        expires_str = (
            new_token_data["expires_at"].strftime("%Y-%m-%d")
            if new_token_data.get("expires_at")
            else "Unknown"
        )
        print("✓ Token refreshed successfully")
        print(f"  New expiration: {expires_str}")
        return 0
    else:
        print("✗ Failed to refresh token")
        return 1


def init_tokens(args):
    """Initialize tokens for all gateways configured in sites.json that don't have tokens."""
    from home_monitor.apis.enphase_local import (
        EnlightenSession,
        get_enlighten_credentials,
    )
    from home_monitor.site_config import load_sites_config

    # Check if credentials are available
    username, password = get_enlighten_credentials()
    if not username or not password:
        print("Error: Enlighten credentials not configured.")
        print("Set ENPHASE_ENLIGHTEN_USERNAME and ENPHASE_ENLIGHTEN_PASSWORD in your .env file.")
        return 1

    # Load sites config to find all gateways
    try:
        sites_config = load_sites_config()
    except Exception as e:
        print(f"Error loading sites.json: {e}")
        return 1

    # Collect all gateways from all sites
    gateways_to_init = []
    for site_name, site_config in sites_config.get("sites", {}).items():
        enphase_local = site_config.get("enphase_local", {})
        gateways = enphase_local.get("gateways", [])
        for gw in gateways:
            serial = gw.get("serial")
            host = gw.get("host")
            if serial and host:
                # Check if token already exists
                existing = get_enphase_gateway_token(serial)
                if not existing:
                    gateways_to_init.append(
                        {
                            "serial": serial,
                            "host": host,
                            "site": site_name,
                        }
                    )
                else:
                    print(f"✓ Gateway {serial} already has a token (site: {site_name})")

    if not gateways_to_init:
        print("\nAll gateways already have tokens.")
        return 0

    print(f"\nFetching tokens for {len(gateways_to_init)} gateway(s) from Enlighten...\n")

    session = EnlightenSession(username, password)
    success_count = 0
    fail_count = 0

    for gw in gateways_to_init:
        serial = gw["serial"]
        host = gw["host"]
        site = gw["site"]
        print(f"Fetching token for {serial} ({host}, site: {site})...", end=" ")

        try:
            token_data = session.get_gateway_token(serial)
            if token_data:
                upsert_enphase_gateway_token(
                    gateway_serial=serial,
                    gateway_host=host,
                    token=token_data["token"],
                    token_expires_at=token_data["expires_at"],
                )
                expires_str = (
                    token_data["expires_at"].strftime("%Y-%m-%d")
                    if token_data.get("expires_at")
                    else "Unknown"
                )
                print(f"✓ Expires: {expires_str}")
                success_count += 1
            else:
                print("✗ Failed to get token")
                fail_count += 1
        except Exception as e:
            print(f"✗ {e}")
            fail_count += 1

    print(f"\nInitialized {success_count} token(s), {fail_count} failed.")
    return 0 if fail_count == 0 else 1


def refresh_all_expiring(args):
    """Refresh all tokens that are expiring within threshold days."""
    from home_monitor.apis.enphase_local import (
        TOKEN_REFRESH_THRESHOLD_DAYS,
        EnlightenSession,
        get_enlighten_credentials,
    )

    # Check if credentials are available
    username, password = get_enlighten_credentials()
    if not username or not password:
        print("Error: Enlighten credentials not configured.")
        print("Set ENPHASE_ENLIGHTEN_USERNAME and ENPHASE_ENLIGHTEN_PASSWORD in your .env file.")
        return 1

    threshold_days = args.days if args.days else TOKEN_REFRESH_THRESHOLD_DAYS
    tokens = get_all_enphase_gateway_tokens()

    if not tokens:
        print("No gateway tokens stored.")
        return 0

    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=threshold_days)
    expiring_tokens = []

    for token_data in tokens:
        expires_at = token_data.get("token_expires_at")
        if expires_at:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= threshold:
                expiring_tokens.append(token_data)

    if not expiring_tokens:
        print(f"No tokens expiring within {threshold_days} days.")
        return 0

    print(f"Found {len(expiring_tokens)} token(s) expiring within {threshold_days} days:\n")

    session = EnlightenSession(username, password)
    success_count = 0
    fail_count = 0

    for token_data in expiring_tokens:
        serial = token_data.get("gateway_serial")
        host = token_data.get("gateway_host")
        expires_at = token_data.get("token_expires_at")
        days_left = (expires_at - now).days if expires_at else 0

        print(f"Refreshing {serial} (expires in {days_left}d)...", end=" ")

        try:
            new_token_data = session.get_gateway_token(serial)
            if new_token_data:
                upsert_enphase_gateway_token(
                    gateway_serial=serial,
                    gateway_host=host,
                    token=new_token_data["token"],
                    token_expires_at=new_token_data["expires_at"],
                )
                print(f"✓ New expiration: {new_token_data['expires_at'].strftime('%Y-%m-%d')}")
                success_count += 1
            else:
                print("✗ Failed")
                fail_count += 1
        except Exception as e:
            print(f"✗ {e}")
            fail_count += 1

    print(f"\nRefreshed {success_count} token(s), {fail_count} failed.")
    return 0 if fail_count == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="Manage Enphase IQ Gateway tokens for local API access",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Store a new gateway token (tokens are valid for 1 year for system owners)
  %(prog)s store --serial 123456789012 --host 192.168.1.100 --token "eyJ..."

  # List all stored tokens
  %(prog)s list

  # Test connectivity to a gateway
  %(prog)s test --serial 123456789012

  # Refresh a specific token (requires ENPHASE_ENLIGHTEN_USERNAME/PASSWORD in .env)
  %(prog)s refresh --serial 123456789012

  # Refresh all tokens
  %(prog)s refresh --all

  # Refresh tokens expiring within N days
  %(prog)s refresh-expiring --days 60

  # Delete a token
  %(prog)s delete --serial 123456789012

Token Generation:
  1. Go to https://entrez.enphaseenergy.com
  2. Log in with your Enlighten account
  3. Select your system and gateway serial number
  4. Click "Create access token" and copy the token

Automatic Token Refresh:
  Configure ENPHASE_ENLIGHTEN_USERNAME and ENPHASE_ENLIGHTEN_PASSWORD in .env
  Tokens will be automatically refreshed when they are within 30 days of expiration.
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Store command
    store_parser = subparsers.add_parser("store", help="Store a gateway token")
    store_parser.add_argument("--serial", required=True, help="Gateway serial number (12 digits)")
    store_parser.add_argument("--host", required=True, help="Gateway IP address or hostname")
    store_parser.add_argument("--token", required=True, help="Gateway access token")
    store_parser.add_argument(
        "--expires-at",
        dest="expires_at",
        help="Token expiration (ISO format or Unix timestamp). Default: 1 year from now",
    )
    store_parser.add_argument(
        "--location-id",
        dest="location_id",
        type=int,
        help="Associated location ID (optional)",
    )

    # List command
    subparsers.add_parser("list", help="List all stored gateway tokens")

    # Test command
    test_parser = subparsers.add_parser("test", help="Test gateway connectivity")
    test_parser.add_argument("--serial", required=True, help="Gateway serial number to test")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a gateway token")
    delete_parser.add_argument("--serial", required=True, help="Gateway serial number to delete")

    # Refresh command
    refresh_parser = subparsers.add_parser(
        "refresh", help="Refresh a gateway token using Enlighten credentials"
    )
    refresh_parser.add_argument(
        "--serial", help="Gateway serial number to refresh (required unless --all)"
    )
    refresh_parser.add_argument(
        "--all", action="store_true", help="Refresh all stored gateway tokens"
    )

    # Refresh-expiring command
    refresh_expiring_parser = subparsers.add_parser(
        "refresh-expiring",
        help="Refresh tokens that are expiring soon",
    )
    refresh_expiring_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Refresh tokens expiring within this many days (default: 30)",
    )

    # Init command - fetch tokens for gateways in sites.json that don't have tokens
    subparsers.add_parser(
        "init",
        help="Fetch tokens from Enlighten for all gateways in sites.json that don't have tokens",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Initialize database
    try:
        init_database()
    except Exception as e:
        print(f"Error initializing database: {e}")
        return 1

    # Run the appropriate command
    if args.command == "store":
        return store_token(args)
    elif args.command == "list":
        return list_tokens(args)
    elif args.command == "test":
        return test_gateway(args)
    elif args.command == "delete":
        return delete_token(args)
    elif args.command == "refresh":
        if not args.all and not args.serial:
            print("Error: --serial or --all is required for refresh command")
            return 1
        return refresh_token(args)
    elif args.command == "refresh-expiring":
        return refresh_all_expiring(args)
    elif args.command == "init":
        return init_tokens(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
