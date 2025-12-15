import asyncio
import telnetlib3
import re
from core.telnet_common import telnet_connect, send_command

# ================== PARSERS ==================
def extract(regex, text, default="N/A"):
    m = re.search(regex, text, re.I)
    return m.group(1) if m else default

def parse_snr_interface(raw):
    data = {}
    data["state"] = extract(r"is\s+(up|down)", raw).upper()

    speed = re.search(r"(?:Auto-speed:|Negotiation\s+)(\d+)", raw)
    data["speed"] = f"{speed.group(1)}M" if speed else "N/A"

    def bits_to_mb(bits):
        return round(int(bits) / 8 / 1024 / 1024, 2)

    def bits_to_bytes(bits):
        return int(bits)

    data["in_5m"] = bits_to_mb(extract(r"5 minute input rate\s+(\d+)", raw, "0"))
    data["out_5m"] = bits_to_mb(extract(r"5 minute output rate\s+(\d+)", raw, "0"))
    data["in_5s"] = bits_to_bytes(extract(r"5 second input rate\s+(\d+)", raw, "0"))
    data["out_5s"] = bits_to_bytes(extract(r"5 second output rate\s+(\d+)", raw, "0"))
    data["input_err"] = extract(r"(\d+)\s+input errors", raw, "0")
    data["output_err"] = extract(r"(\d+)\s+output errors", raw, "0")
    data["crc"] = extract(r"(\d+)\s+CRC", raw, "0")

    return data

def parse_snr_mac(raw: str):
    for line in raw.splitlines():
        cols = line.split()
        if (
            not cols
            or all(c == '-' for c in cols[0])
            or "vlan" in line.lower()
            or "mac address" in line.lower()
            or cols[0].lower().startswith("show")
            or cols[0].lower().startswith("read")
        ):
            continue

        if len(cols) >= 2 and "-" in cols[1] and ":" not in cols[1]:
            return {"vlan": cols[0], "mac": cols[1]}

    return None

def parse_snr_logs(raw, port, limit=15):
    logs_short = []
    port_digits = re.escape(str(port))

    pattern = re.compile(
        rf"(\d+)\s+(%[A-Za-z]+\s+\d+\s+\d+:\d+:\d+).*?Ethernet{port_digits}.*?(UP|DOWN)",
        re.IGNORECASE
    )

    for line in raw.splitlines():
        match = pattern.search(line)
        if match:
            log_id = match.group(1)
            dt = match.group(2)
            state = match.group(3).upper()
            logs_short.append(f"{log_id} {dt} - {state}")

        if len(logs_short) >= limit:
            break

    return logs_short

def parse_snr_model(raw):
    match = re.search(r"SNR-[\w]+", raw)
    return match.group(0) if match else "N/A"

# ================== OUTPUT ==================
def print_report(port, iface, mac, logs_short, base_info):
    print(f'\n------------ [PORT {port}] ------------')
    print("\n===== DEVICE INFO =====")
    print(f"MODEL   : {base_info['model']}")
    print(f"VERSION : {base_info.get('version', 'N/A')}")

    print("\n===== LINK =====")
    print(f"STATE : {iface['state']}")

    if iface["state"] == "DOWN":
        print("\n[L1] Нет линка. Возможна физическая проблема.")
        print('❌ Порт не активен (DOWN)')
        print('Проверьте кабель / питание / подключение роутера')
    else:
        print(f"SPEED : {iface['speed']}")
        print(f"\n===== PORT MAC/VLAN =====")
        if mac:
            print(f"MAC  : {mac['mac']}")
            print(f"VLAN : {mac['vlan']}")
        else:
            print("MAC не найден")

        print("\n===== PORT TRAFFIC =====")
        print(f"IN  (5m) : {iface['in_5m']} MB/s")
        print(f"OUT (5m) : {iface['out_5m']} MB/s")
        print(f"IN  (5s) : {iface['in_5s']} bytes/s")
        print(f"OUT (5s) : {iface['out_5s']} bytes/s")

        print("\n===== PORT ERRORS =====")
        print(f"INPUT  ERR : {iface['input_err']}")
        print(f"OUTPUT ERR : {iface['output_err']}")
        print(f"CRC        : {iface['crc']}")

    # Логи выводим всегда
    print("\n===== DEVICE LOGS =====")
    if logs_short:
        for l in logs_short:
            print(l)
    else:
        print("Логи не найдены")

# ================== RUN ==================
async def run(host: str, password: str, port: str):
    # Добавляем префикс для порта, если нужно
    if "/" not in port:
        port = f"1/0/{port}"

    reader, writer = await telnet_connect(host, password)

    # ===== PORT COMMANDS =====
    port_commands = {
        "iface": f"show interface ethernet {port}",
        "mac": f"show mac-address-table interface ethernet {port}",
        "logs": "show logging flash"
    }

    # ===== BASE COMMANDS =====
    base_commands = {
        "version": "show version",
        "system": "show system",
        "switch": "show switch"
    }

    data = {}
    for key, cmd in port_commands.items():
        data[key] = await send_command(reader, writer, cmd)

    for key, cmd in base_commands.items():
        data[key] = await send_command(reader, writer, cmd)

    # Парсим модель
    model = parse_snr_model(data["version"])
    if not model.startswith("SNR-"):
        print(f"\nУстройство {host} не является оборудованием SNR. Диагностика пропущена.")
        writer.close()
        return

    # Продолжаем диагностику
    iface = parse_snr_interface(data["iface"])
    mac = parse_snr_mac(data["mac"])
    logs_short = parse_snr_logs(data["logs"], port)

    base_info = {
        "model": model,
        "version": extract(r"SoftWare Version ([\d\.]+)", data["version"], "N/A")
    }

    print_report(port, iface, mac, logs_short, base_info)
    writer.close()
