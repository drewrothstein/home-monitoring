#!/usr/bin/env python3
"""
Helper script to obtain Flume OAuth access token.

Flume uses OAuth 2 Resource Owner Password Credentials Grant, which means
you authenticate directly with your Flume username and password.

Prerequisites:
1. Create a personal API client at https://portal.flumewater.com/ (Settings > API Access)
2. You'll receive: Client ID and Client Secret
3. Use your Flume account email and password for authentication

Usage:
    # Get initial tokens (stores automatically in database):
    python scripts/get_flume_token.py --username YOUR_EMAIL
    # (password will be prompted securely)

    # Or with password as argument (not recommended):
    python scripts/get_flume_token.py --username YOUR_EMAIL --password YOUR_PASSWORD

    # Refresh token:
    python scripts/get_flume_token.py --refresh-token REFRESH_TOKEN

    # List your devices (after getting tokens):
    python scripts/get_flume_token.py --list-devices
"""

import argparse
import getpass
import os
import sys

from dotenv import load_dotenv

# Add parent directory to path to import home_monitor modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file if it exists (for local development)
load_dotenv()

from home_monitor.apis.flume import (  # noqa: E402
    FlumeApiClient,
    _decode_jwt_payload,
    get_tokens,
    refresh_access_token,
)


def main():
    parser = argparse.ArgumentParser(
        description="Obtain or refresh Flume OAuth tokens",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--client-id",
        help="Client ID from Flume API Access settings (or set FLUME_CLIENT_ID in .env)",
    )
    parser.add_argument(
        "--client-secret",
        help="Client Secret from Flume API Access settings (or set FLUME_CLIENT_SECRET in .env)",
    )
    parser.add_argument(
        "--username",
        help="Flume account email (or set FLUME_USERNAME in .env)",
    )
    parser.add_argument(
        "--password",
        help="Flume account password (or set FLUME_PASSWORD in .env)",
    )
    parser.add_argument(
        "--location-id",
        type=int,
        help="Location ID to use if creating Flume config (optional, tokens are stored globally)",
    )
    parser.add_argument(
        "--refresh-token",
        help="Refresh token to get new access token",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List all Flume devices for the authenticated user",
    )

    args = parser.parse_args()

    # Get credentials from args or environment
    client_id = args.client_id or os.getenv("FLUME_CLIENT_ID")
    client_secret = args.client_secret or os.getenv("FLUME_CLIENT_SECRET")
    username = args.username or os.getenv("FLUME_USERNAME")
    password = args.password  # Password is prompted interactively if not provided

    # List devices using stored tokens
    if args.list_devices:
        try:
            from home_monitor.config import get_flume_credentials

            access_token, user_id = get_flume_credentials()
            if not access_token:
                print("❌ ERROR: No Flume access token found in database")
                print("Run 'make flume-token' to set up authentication first")
                sys.exit(1)

            client = FlumeApiClient(access_token=access_token, user_id=user_id)
            devices = client.get_devices()

            print("\n" + "=" * 70)
            print("📱 FLUME DEVICES")
            print("=" * 70)
            sensor_ids = []
            if devices:
                for device in devices:
                    device_id = device.get("id", "unknown")
                    device_type = device.get("type", "unknown")
                    product = device.get("product", "unknown")
                    connected = device.get("connected", False)
                    battery = device.get("battery_level")

                    # Type 1 = Bridge (WiFi gateway), Type 2 = Sensor (water meter)
                    if device_type == 1:
                        type_label = "Bridge (WiFi gateway)"
                    elif device_type == 2:
                        type_label = "Sensor (water meter) ✓ USE THIS ID"
                        sensor_ids.append(device_id)
                    else:
                        type_label = f"Unknown (type {device_type})"

                    print(f"\n  Device ID: {device_id}")
                    print(f"  Type: {type_label}")
                    print(f"  Product: {product}")
                    print(f"  Connected: {'✓' if connected else '✗'}")
                    if battery:
                        print(f"  Battery: {battery}")
            else:
                print("\n  No devices found")

            if sensor_ids:
                print("\n" + "-" * 70)
                print("💡 Add the SENSOR device ID to your sites.json:")
                print(f'   "flume": {{ "device_id": "{sensor_ids[0]}" }}')
            print("\n" + "=" * 70 + "\n")
            return
        except Exception as e:
            print(f"❌ ERROR: Failed to list devices: {e}")
            sys.exit(1)

    # Validate credentials
    if not client_id:
        print("❌ ERROR: Client ID is required")
        print("")
        print("Set it in your .env file:")
        print("  FLUME_CLIENT_ID=your_client_id")
        print("")
        print("Or pass it as an argument:")
        print("  --client-id your_client_id")
        print("")
        print("Get your Client ID from https://portal.flumewater.com/ (Settings > API Access)")
        sys.exit(1)

    if not client_secret:
        print("❌ ERROR: Client Secret is required")
        print("")
        print("Set it in your .env file:")
        print("  FLUME_CLIENT_SECRET=your_client_secret")
        print("")
        print("Or pass it as an argument:")
        print("  --client-secret your_client_secret")
        sys.exit(1)

    # Refresh token
    if args.refresh_token:
        try:
            token_data = refresh_access_token(client_id, client_secret, args.refresh_token)
            print("\n" + "=" * 70)
            print("✅ TOKEN REFRESHED SUCCESSFULLY")
            print("=" * 70)

            # Try to store in database
            try:
                from home_monitor.token_manager import store_flume_tokens_initial

                access_token = token_data.get("access_token")
                new_refresh_token = token_data.get("refresh_token", args.refresh_token)

                # Extract user_id from JWT
                user_id = None
                if access_token:
                    jwt_payload = _decode_jwt_payload(access_token)
                    user_id = str(jwt_payload.get("user_id", ""))

                store_flume_tokens_initial(
                    access_token=access_token,
                    refresh_token=new_refresh_token,
                    user_id=user_id,
                )
                print("\n✅ Tokens stored in database globally (updated all Flume configs)")
            except Exception as db_error:
                print(f"\n⚠️  WARNING: Could not store tokens in database: {db_error}")

            print("\n" + "=" * 70 + "\n")
        except Exception as e:
            print(f"❌ ERROR: Failed to refresh token: {e}")
            sys.exit(1)
        return

    # Get token using username/password
    if not username:
        print("❌ ERROR: Username (email) is required")
        print("")
        print("Set it in your .env file:")
        print("  FLUME_USERNAME=your_email@example.com")
        print("")
        print("Or pass it as an argument:")
        print("  --username your_email@example.com")
        sys.exit(1)

    if not password:
        # Prompt for password securely
        print(f"\n🔐 Authenticating as: {username}")
        password = getpass.getpass("Enter your Flume password: ")
        if not password:
            print("❌ ERROR: Password is required")
            sys.exit(1)

    try:
        token_data = get_tokens(client_id, client_secret, username, password)
        print("\n" + "=" * 70)
        print("✅ TOKEN OBTAINED SUCCESSFULLY")
        print("=" * 70)

        access_token = token_data.get("access_token")
        refresh_token_value = token_data.get("refresh_token")

        # Extract user_id from JWT
        user_id = None
        if access_token:
            jwt_payload = _decode_jwt_payload(access_token)
            user_id = str(jwt_payload.get("user_id", ""))
            print(f"\nUser ID: {user_id}")

        # Try to store in database
        try:
            from home_monitor.database import (
                get_any_flume_config_with_tokens,
                get_location_api_config,
                get_locations,
            )
            from home_monitor.manage import add_api_config
            from home_monitor.token_manager import store_flume_tokens_initial

            # Ensure at least one Flume config exists
            if not get_any_flume_config_with_tokens():
                # Check if any config exists at all (even without tokens)
                has_any_config = False
                locations = get_locations()
                if locations:
                    for location in locations:
                        config = get_location_api_config(location["id"], "flume")
                        if config:
                            has_any_config = True
                            break
                    if not has_any_config:
                        # Create config for first location
                        if args.location_id:
                            location_id = args.location_id
                        else:
                            location_id = locations[0]["id"]
                        print(f"\nCreating Flume API config for location_id={location_id}...")
                        add_api_config(location_id, "flume", {})

            store_flume_tokens_initial(
                access_token=access_token,
                refresh_token=refresh_token_value,
                user_id=user_id,
            )
            print("\n✅ Tokens stored in database globally (updated all Flume configs)")
            print("(Client ID and Client Secret still need to be in .env for token refresh)")
        except Exception as db_error:
            error_msg = str(db_error)
            print(f"\n❌ ERROR: Could not store tokens in database: {db_error}")

            # Provide helpful guidance for common connection issues
            if "postgres" in error_msg.lower() and (
                "nodename" in error_msg.lower()
                or "could not translate host name" in error_msg.lower()
            ):
                print("\n💡 TIP: The hostname 'postgres' only works inside Docker containers.")
                print("   When running from your local machine, use 'localhost' instead:")
                print("   ")
                print(
                    "   DATABASE_URL=postgresql://home_monitor:home_monitor_password@localhost:5432/home_monitor"
                )
                print("   ")
                print("   Or run the command inside a Docker container:")
                print(
                    "   docker-compose run --rm fetcher python scripts/get_flume_token.py --username YOUR_EMAIL --password YOUR_PASSWORD"
                )
            else:
                print(
                    "\nTokens must be stored in the database. Please ensure the database is accessible and try again."
                )
            sys.exit(1)

        # List devices
        print("\n📱 Your Flume Devices:")
        sensor_id = None
        try:
            client = FlumeApiClient(access_token=access_token, user_id=user_id)
            devices = client.get_devices()
            if devices:
                for device in devices:
                    device_id = device.get("id", "unknown")
                    device_type = device.get("type", "unknown")
                    product = device.get("product", "unknown")

                    # Type 1 = Bridge (WiFi gateway), Type 2 = Sensor (water meter)
                    if device_type == 1:
                        type_label = "Bridge"
                    elif device_type == 2:
                        type_label = "Sensor ✓"
                        sensor_id = device_id
                    else:
                        type_label = f"Type {device_type}"

                    print(f"   • {device_id} ({type_label}, {product})")

                if sensor_id:
                    print("\n💡 Add the SENSOR device ID to your sites.json:")
                    print(f'   "flume": {{ "device_id": "{sensor_id}" }}')
            else:
                print("   No devices found")
        except Exception as e:
            print(f"   Could not list devices: {e}")

        print("\n" + "=" * 70 + "\n")
    except Exception as e:
        print(f"❌ ERROR: Failed to get token: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
