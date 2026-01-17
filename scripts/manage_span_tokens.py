#!/usr/bin/env python3
"""
Manage Span Power Panel tokens for local API access.

This script allows you to:
- Register a new client with a panel (requires physical button press)
- Store existing tokens in the database
- List stored panel tokens
- Test panel connectivity
- Delete panel tokens

Usage:
    # Register a new client with a panel (panel must be unlocked)
    python scripts/manage_span_tokens.py register --host 192.168.1.200 --name "Main Panel"

    # Store an existing token
    python scripts/manage_span_tokens.py store --host 192.168.1.200 --token "your_token" --name "Main Panel"

    # List all stored panel tokens
    python scripts/manage_span_tokens.py list

    # Test connectivity to a panel
    python scripts/manage_span_tokens.py test --host 192.168.1.200

    # Delete a panel token
    python scripts/manage_span_tokens.py delete --serial nt-2243-001cx
"""

import argparse
import sys
from datetime import datetime, timezone

# Add parent directory to path for imports
sys.path.insert(0, ".")

from home_monitor.apis.span import SpanPanelClient  # noqa: E402
from home_monitor.database import (  # noqa: E402
    delete_span_panel_token,
    get_all_span_panel_tokens,
    get_span_panel_token_by_host,
    init_database,
    upsert_span_panel_token,
)
from home_monitor.site_config import ensure_site_in_database  # noqa: E402


def register_client(args):
    """Register a new client with the panel."""
    print(f"\n🔐 Registering client with Span panel at {args.host}")
    print("=" * 60)
    print()
    print("⚠️  IMPORTANT: The panel must be UNLOCKED before registering.")
    print()
    print("To unlock the panel:")
    print("  1. Open the panel door")
    print("  2. Press the door sensor button (at the top) 3 times")
    print("     within 15 seconds")
    print("  3. Wait for the frame lights to blink (confirms unlock)")
    print("  4. The panel stays unlocked for 15 minutes")
    print()

    # Check if panel is reachable first
    print(f"📡 Checking if panel at {args.host} is reachable...")
    client = SpanPanelClient(panel_host=args.host)

    if not client.check_connection():
        print(f"❌ Cannot reach panel at {args.host}")
        print("   Verify the IP address is correct and the panel is on the network.")
        return 1

    # Get status to show panel info
    try:
        status = client.get_status()
        system = status.get("system", {})
        software = status.get("software", {})
        panel_serial = system.get("serial", "unknown")
        firmware = software.get("firmwareVersion", "unknown")
        print(f"✓ Panel found: {panel_serial} (firmware: {firmware})")
    except Exception as e:
        print(f"⚠️  Could not get panel status: {e}")
        panel_serial = None

    print()
    input("Press Enter when you have unlocked the panel (or Ctrl+C to cancel)...")
    print()

    # Generate a unique client name
    client_name = f"home-monitor-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    client_description = args.name or f"Home Monitor at {args.host}"

    print(f"📝 Registering client '{client_name}'...")

    try:
        result = SpanPanelClient.register_client(
            panel_host=args.host,
            client_name=client_name,
            client_description=client_description,
        )

        token = result.get("accessToken")
        if not token:
            print("❌ Registration failed: No token returned")
            print(f"   Response: {result}")
            return 1

        # Get the serial from status if we didn't get it earlier
        if not panel_serial:
            try:
                client_with_token = SpanPanelClient(panel_host=args.host, token=token)
                status = client_with_token.get_status()
                panel_serial = status.get("system", {}).get("serial", "unknown")
            except Exception:
                panel_serial = f"unknown-{args.host.replace('.', '-')}"

        # Get location_id if specified
        location_id = None
        if args.location:
            try:
                location_id = ensure_site_in_database(args.location)
                print(f"✓ Associated with location: {args.location} (ID: {location_id})")
            except Exception as e:
                print(f"⚠️  Could not find location '{args.location}': {e}")

        # Store token in database
        token_id = upsert_span_panel_token(
            panel_serial=panel_serial,
            panel_host=args.host,
            panel_name=args.name or client_description,
            token=token,
            token_created_at=datetime.now(timezone.utc),
            location_id=location_id,
        )

        print()
        print("✅ Registration successful!")
        print(f"   Panel Serial: {panel_serial}")
        print(f"   Panel Name: {args.name or client_description}")
        print(f"   Host: {args.host}")
        if location_id:
            print(f"   Location: {args.location} (ID: {location_id})")
        print(f"   Database ID: {token_id}")
        print()
        print("The token has been stored in the database.")
        print("The fetcher will now be able to collect data from this panel.")
        return 0

    except Exception as e:
        print(f"❌ Registration failed: {e}")
        print()
        print("If the panel is not unlocked, press the door button 3 times and try again.")
        return 1


def store_token(args):
    """Store an existing token in the database."""
    print(f"📝 Storing token for panel at {args.host}...")

    # Try to get panel serial from the API
    panel_serial = args.serial
    if not panel_serial:
        try:
            client = SpanPanelClient(panel_host=args.host, token=args.token)
            status = client.get_status()
            panel_serial = status.get("system", {}).get("serial")
        except Exception as e:
            print(f"⚠️  Could not get panel serial from API: {e}")

    if not panel_serial:
        panel_serial = f"unknown-{args.host.replace('.', '-')}"
        print(f"⚠️  Using generated serial: {panel_serial}")

    # Get location_id if specified
    location_id = None
    if args.location:
        try:
            location_id = ensure_site_in_database(args.location)
        except Exception as e:
            print(f"⚠️  Could not find location '{args.location}': {e}")

    token_id = upsert_span_panel_token(
        panel_serial=panel_serial,
        panel_host=args.host,
        panel_name=args.name,
        token=args.token,
        token_created_at=datetime.now(timezone.utc),
        location_id=location_id,
    )

    print(f"✓ Stored token for panel {panel_serial}")
    print(f"  Host: {args.host}")
    print(f"  Name: {args.name or '(not set)'}")
    if location_id:
        print(f"  Location: {args.location} (ID: {location_id})")
    print(f"  Database ID: {token_id}")
    return 0


def list_tokens(args):
    """List all stored panel tokens."""
    tokens = get_all_span_panel_tokens()

    if not tokens:
        print("No Span panel tokens stored.")
        print()
        print("To register a new panel:")
        print('  make span-register HOST=192.168.1.200 NAME="Main Panel"')
        return 0

    print()
    print(f"{'Name':<20} {'Serial':<18} {'Host':<16} {'Created':<12}")
    print("-" * 70)

    for token in tokens:
        name = token.get("panel_name") or "(unnamed)"
        if len(name) > 19:
            name = name[:16] + "..."
        serial = token.get("panel_serial", "?")
        host = token.get("panel_host", "?")
        created_at = token.get("created_at")

        if created_at:
            created_str = created_at.strftime("%Y-%m-%d")
        else:
            created_str = "Unknown"

        print(f"{name:<20} {serial:<18} {host:<16} {created_str:<12}")

    print()
    return 0


def test_panel(args):
    """Test connectivity to a panel."""
    # Get token from database
    token_data = get_span_panel_token_by_host(args.host)

    if not token_data:
        print(f"⚠️  No token found for panel at {args.host}")
        print("   Testing without authentication (status endpoint only)...")
        print()
        token = None
        panel_name = args.host
    else:
        token = token_data.get("token")
        panel_name = token_data.get("panel_name") or args.host

    print(f"🔍 Testing connection to panel '{panel_name}' at {args.host}...")
    print()

    client = SpanPanelClient(
        panel_host=args.host,
        token=token,
        panel_name=panel_name,
    )

    # Test basic connectivity (no auth required)
    if client.check_connection():
        print("✓ Panel is reachable")
    else:
        print("✗ Cannot reach panel")
        return 1

    # Get status
    try:
        status = client.get_status()
        system = status.get("system", {})
        software = status.get("software", {})
        network = status.get("network", {})

        print()
        print("Panel Information:")
        print(f"  Serial: {system.get('serial')}")
        print(f"  Model: {system.get('model')}")
        print(f"  Firmware: {software.get('firmwareVersion')}")
        print(f"  Door State: {system.get('doorState')}")
        print(f"  Uptime: {system.get('uptime')} seconds")
        print()
        print("Network Status:")
        print(f"  Ethernet: {'Connected' if network.get('eth0Link') else 'Disconnected'}")
        print(f"  WiFi: {'Connected' if network.get('wlanLink') else 'Disconnected'}")
        print(f"  Cellular: {'Connected' if network.get('wwanLink') else 'Disconnected'}")

    except Exception as e:
        print(f"⚠️  Could not get status: {e}")

    # Test authenticated endpoints if we have a token
    if token:
        print()
        print("Testing authenticated endpoints...")

        try:
            panel = client.get_panel()
            print(f"✓ Panel endpoint: Grid power = {panel.get('instantGridPowerW'):.1f}W")
        except Exception as e:
            print(f"✗ Panel endpoint failed: {e}")
            return 1

        try:
            circuits = client.get_circuits()
            spaces = circuits.get("spaces", {})
            print(f"✓ Circuits endpoint: {len(spaces)} circuits found")

            # Show top 5 circuits by power
            circuit_list = []
            for circuit_id, data in spaces.items():
                power = data.get("instantPowerW", 0)
                name = data.get("name", circuit_id)
                circuit_list.append((name, power))

            circuit_list.sort(key=lambda x: abs(x[1]), reverse=True)

            if circuit_list:
                print()
                print("Top circuits by power:")
                for name, power in circuit_list[:5]:
                    print(f"    {name}: {power:.1f}W")

        except Exception as e:
            print(f"✗ Circuits endpoint failed: {e}")

        try:
            storage = client.get_storage_soe()
            if storage:
                soe = storage.get("soe", {})
                print(f"✓ Storage endpoint: Battery at {soe.get('percentage')}%")
            else:
                print("ℹ️  No battery/storage connected")
        except Exception as e:
            print(f"ℹ️  Storage endpoint: {e}")

    print()
    print("✅ Panel test complete")
    return 0


def delete_token(args):
    """Delete a panel token."""
    if delete_span_panel_token(args.serial):
        print(f"✓ Deleted token for panel {args.serial}")
        return 0
    else:
        print(f"⚠️  No token found for panel {args.serial}")
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage Span Power Panel tokens",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Register a new client (requires panel to be unlocked)
  python scripts/manage_span_tokens.py register --host 192.168.1.200 --name "Main Panel"

  # Store an existing token
  python scripts/manage_span_tokens.py store --host 192.168.1.200 --token "eyJ..." --name "Main Panel"

  # List all stored tokens
  python scripts/manage_span_tokens.py list

  # Test connectivity to a panel
  python scripts/manage_span_tokens.py test --host 192.168.1.200

  # Delete a token
  python scripts/manage_span_tokens.py delete --serial nt-2243-001cx
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Register command
    register_parser = subparsers.add_parser("register", help="Register a new client with a panel")
    register_parser.add_argument("--host", required=True, help="Panel IP address or hostname")
    register_parser.add_argument("--name", help="Human-readable name for the panel")
    register_parser.add_argument("--location", help="Site name to associate with (e.g., FL, NY)")

    # Store command
    store_parser = subparsers.add_parser("store", help="Store an existing token in the database")
    store_parser.add_argument("--host", required=True, help="Panel IP address or hostname")
    store_parser.add_argument("--token", required=True, help="Panel access token")
    store_parser.add_argument("--name", help="Human-readable name for the panel")
    store_parser.add_argument("--location", help="Site name to associate with (e.g., FL, NY)")
    store_parser.add_argument(
        "--serial", help="Panel serial number (auto-detected if not provided)"
    )

    # List command
    subparsers.add_parser("list", help="List all stored panel tokens")

    # Test command
    test_parser = subparsers.add_parser("test", help="Test connectivity to a panel")
    test_parser.add_argument("--host", required=True, help="Panel IP address or hostname")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a panel token")
    delete_parser.add_argument("--serial", required=True, help="Panel serial number")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Initialize database
    try:
        init_database()
    except Exception as e:
        print(f"❌ Failed to initialize database: {e}")
        return 1

    # Run command
    if args.command == "register":
        return register_client(args)
    elif args.command == "store":
        return store_token(args)
    elif args.command == "list":
        return list_tokens(args)
    elif args.command == "test":
        return test_panel(args)
    elif args.command == "delete":
        return delete_token(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
