from core.telnet_common import send_command, telnet_connect

async def detect_vendor(host: str, password: str):
    reader, writer = await telnet_connect(host, password)
    output = await send_command(reader, writer, "show system", timeout=1.5)
    writer.close()

    if "MES" in output:
        return "ELTEX"
    elif "ZTE" in output:
        return "ZTE"
    elif "SNR" in output:
        return "SNR"
    elif "DES" in output:
        return "D-LINK"
    elif "DGS" in output:
        return "D-LINK"
    else:
        return "UNKNOWN"