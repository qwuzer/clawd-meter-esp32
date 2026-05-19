#!/usr/bin/env python3
"""Scan for all nearby BLE devices and print name + service UUIDs. Run this to debug."""
import asyncio
from bleak import BleakScanner

OUR_UUID = "4c41555a-4465-7669-6365-000000000001"

async def main():
    print("Scanning for 10 seconds...")
    found = {}

    def callback(device, adv):
        found[device.address] = (device.name, adv.service_uuids)

    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(10)

    if not found:
        print("No BLE devices found.")
        print("-> System Settings > Privacy & Security > Bluetooth")
        print("   Make sure Terminal is listed and toggled ON.")
        return

    print(f"\nFound {len(found)} device(s):  (* = our service UUID)\n")
    for addr, (name, uuids) in found.items():
        marker = "*" if OUR_UUID in [u.lower() for u in uuids] else " "
        label  = name or "(no name)"
        print(f"  {marker} {addr}  {label}")
        for u in uuids:
            flag = "  <-- OURS" if u.lower() == OUR_UUID else ""
            print(f"        {u}{flag}")

asyncio.run(main())
