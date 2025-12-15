# dlink_diag.py
import asyncio, re
from core.telnet_common import telnet_connect

# ================== ANSI CLEAN ==================
ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def clean_line(line):
    line = ansi_escape.sub('', line)
    line = re.sub(r"[^\x20-\x7E]+", " ", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()

def extract_speed(port_info_line):
    match = re.search(r'\b\d+(?:M|G)\b', port_info_line)
    if match:
        return match.group(0)
    return None

# ================== TELNET COMMANDS ==================
async def get_telnet_output(host, password, command):
    reader, writer = await telnet_connect(host, password)

    writer.write(f"{command}\n")
    await asyncio.sleep(0.5)

    output_lines = []
    more_triggered = False

    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            if not chunk:
                break

            lines = chunk.splitlines()
            for line in lines:
                cleaned = clean_line(line)
                if cleaned:
                    output_lines.append(cleaned)

            more_markers = ["----", "Next Page", "Press any key", "SPACE", "CTRL+C"]
            if any(marker in chunk for marker in more_markers) and not more_triggered:
                writer.write("q")
                await asyncio.sleep(0.2)
                more_triggered = True

            if any(line.endswith("/ME") for line in lines):
                break

        except asyncio.TimeoutError:
            break

    writer.close()
    await asyncio.sleep(0.2)
    return output_lines

# ================== PORT FUNCTIONS ==================
async def show_ports_speed(host, password, port):
    lines = await get_telnet_output(host, password, f"show ports {port}")

    port_data = []
    collecting = False
    for line in lines:
        if line.startswith(str(port) + " "):
            port_data.append(line)
            collecting = True
        elif collecting and line and not line.endswith("/ME"):
            port_data.append(line)
        elif collecting:
            break

    full_port_info = " ".join(port_data)
    return extract_speed(full_port_info)

async def get_port_macs(host, password, port):
    lines = await get_telnet_output(host, password, f"show fdb port {port}")

    mac_table = []
    mac_regex = re.compile(r'([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}')

    for line in lines:
        cols = line.split()
        if len(cols) < 4:
            continue
        vid_candidate = cols[0]
        mac_candidate = cols[2]

        if vid_candidate.isdigit() and mac_regex.fullmatch(mac_candidate):
            mac_table.append({'vid': vid_candidate, 'mac': mac_candidate})
    return mac_table

async def get_port_bytes(host, password, port):
    async def run_telnet_raw():
        reader, writer = await telnet_connect(host, password)

        writer.write(f"show packet ports {port}\n")
        await asyncio.sleep(0.3)
        writer.write("q")
        await asyncio.sleep(0.15)
        writer.write("\x03")
        await asyncio.sleep(0.25)

        output_chunks = []
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                if not chunk:
                    break
                output_chunks.append(chunk)

                if any(line.endswith("/ME") for line in chunk.splitlines()):
                    break
            except asyncio.TimeoutError:
                break

        writer.close()
        await asyncio.sleep(0.05)
        return "".join(output_chunks)

    raw = await run_telnet_raw()
    raw_lines = raw.splitlines()
    clean_lines = [clean_line(l) for l in raw_lines if clean_line(l)]

    start_idx = None
    for i, l in enumerate(clean_lines):
        if l.startswith(f"Port Number : {port}"):
            start_idx = i
            break

    if start_idx is None:
        return None, None

    end_idx = len(clean_lines)
    for j in range(start_idx + 1, len(clean_lines)):
        if any(clean_lines[j].startswith(x) for x in ["Unicast", "Multicast", "Broadcast", "Port Number :", "/ME"]):
            end_idx = j
            break

    block = clean_lines[start_idx:end_idx]
    rx_bytes = tx_bytes = None

    for line in block:
        if "RX Bytes" in line and rx_bytes is None:
            nums = re.findall(r'\d+', line)
            if nums:
                rx_bytes = int(nums[-1])

        if "TX Bytes" in line and tx_bytes is None:
            nums = re.findall(r'\d+', line)
            if nums:
                tx_bytes = int(nums[-1])

        if rx_bytes is not None and tx_bytes is not None:
            break

    return rx_bytes, tx_bytes

async def get_port_errors(host, password, port):
    lines = await get_telnet_output(host, password, f"show error ports {port}")

    port_data = []
    collecting = False
    for line in lines:
        if re.search(rf"\b{port}\b", line) or "RX Frames" in line or "TX Frames" in line:
            port_data.append(line)
            collecting = True
        elif collecting and line and not line.endswith("/ME"):
            port_data.append(line)
        elif collecting:
            break

    if not port_data:
        return 0, 0

    rx_crc = tx_crc = 0
    for line in port_data:
        if line.startswith("CRC Error") and not "TX Frames" in line:
            parts = line.split()
            if parts[-1].isdigit():
                rx_crc = int(parts[-1])
        elif "CRC Error" in line and "TX Frames" in line:
            parts = line.split()
            if parts[-1].isdigit():
                tx_crc = int(parts[-1])

    return rx_crc, tx_crc

async def get_device_logs(host, password, port, max_logs=15):
    reader, writer = await telnet_connect(host, password)
    writer.write("show log\n")
    await asyncio.sleep(0.5)

    logs = []
    port_patterns = [
        rf"\bport\s+{port}\b",
        rf"\bPort\s+{port}\b",
        rf"\bPort Number\s*:\s*{port}\b"
    ]
    port_regex = re.compile("|".join(port_patterns), re.IGNORECASE)

    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            if not chunk:
                break

            for line in chunk.splitlines():
                cleaned = clean_line(line)
                if not cleaned:
                    continue
                if any(x in cleaned for x in ["CTRL+C", "ESC", "Quit", "Next Page", "SPACE", "Enter"]):
                    continue
                if port_regex.search(cleaned):
                    logs.append(cleaned)

            more_markers = ["----", "Next Page", "Press any key", "SPACE", "CTRL+C"]
            if any(marker in chunk for marker in more_markers):
                writer.write(" ")
                await asyncio.sleep(0.2)
                continue

            if any(line.endswith("/ME") for line in chunk.splitlines()):
                break

        except asyncio.TimeoutError:
            break

    writer.close()
    await asyncio.sleep(0.2)
    return logs[-max_logs:]

# ================== MODEL / SERIAL ==================
async def get_switch_model_serial(host, password, commands):
    lines = await get_telnet_output(host, password, commands["switch"])
    model = serial = None

    for line in lines:
        line_clean = clean_line(line)
        if not line_clean:
            continue

        # Ищем Device Type
        m = re.search(r'Device Type\s*:\s*(\S+)', line_clean, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            if "DGS" in candidate.upper():
                model = candidate
            elif "DES" in candidate.upper():
                model = candidate

        # Ищем Serial Number
        s = re.search(r'System Serial Number\s*:\s*(\S+)', line_clean, re.IGNORECASE)
        if s:
            serial = s.group(1)

        if model and serial:
            break

    return model, serial

# ================== RUN ==================
async def run(host, password, port):
    commands = {
        "version": "show version",
        "system": "show system",
        "switch": "show switch"
    }

    try:
        await get_telnet_output(host, password, commands["switch"])
    except Exception:
        print("\n❌ Устройство не определено как D-Link. Скрипт завершён.")
        return

    speed = await show_ports_speed(host, password, port)
    if not speed:
        print(f"\n===== PORT STATUS =====\n❌ Порт {port} не активен (DOWN). Проверьте кабель / питание / подключение роутера")
        logs = await get_device_logs(host, password, port)
        print(f"\n===== DEVICE LOGS =====")
        for log in logs or []:
            print(log)
        return

    print(f"\n===== PORT SPEED =====\nПорт: {port}\nСостояние порта: UP\nСкорость порта: {speed}")

    mac_table = await get_port_macs(host, password, port)
    print(f"\n===== PORT MAC/VLAN =====")
    for entry in mac_table or []:
        print(f"MAC: {entry['mac']}\nVLAN: {entry['vid']}")

    rx_bytes, tx_bytes = await get_port_bytes(host, password, port)
    print(f"\n===== PORT TRAFFIC BYTES (Total/5sec) =====")
    print(f"RX Bytes (5s): {rx_bytes if rx_bytes is not None else 'Не найдено'}")
    print(f"TX Bytes (5s): {tx_bytes if tx_bytes is not None else 'Не найдено'}")

    rx_crc, tx_crc = await get_port_errors(host, password, port)
    print(f"\n===== PORT ERROR CRC =====\nCRC Error: {rx_crc} (RX)\nCRC Error: {tx_crc} (TX)")

    logs = await get_device_logs(host, password, port)
    print(f"\n===== DEVICE LOGS =====")
    for log in logs or []:
        print(log)

# ================== MAIN ==================
if __name__ == "__main__":
    host = input("IP устройства: ").strip()
    password = input("Пароль: ").strip()
    port = input("Порт (например 2): ").strip()
    asyncio.run(run(host, password, port))
