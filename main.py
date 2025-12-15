import sys, asyncio
from core.detect_vendor import detect_vendor
from vendors import eltex_diag, zte_diag, snr_diag, dlink_diag

VENDOR_MODULES = {
    "ELTEX": eltex_diag,
    "ZTE": zte_diag,
    "SNR": snr_diag,
    "D-LINK": dlink_diag,
}

async def main():
    if len(sys.argv) != 3:
        print("Использование: python3 main.py <IP> <PORT>")
        sys.exit(1)

    host = sys.argv[1]
    port = sys.argv[2]

    password = "asdzx1390"

    vendor = await detect_vendor(host, password)
    print("ОПРЕДЕЛЕНО:", vendor)

    if vendor in VENDOR_MODULES:
        module = VENDOR_MODULES[vendor]
        await module.run(host, password, port)
    else:
        print(f"❌ Устройство {host} не поддерживается или не определено.")

if __name__ == "__main__":
    asyncio.run(main())
