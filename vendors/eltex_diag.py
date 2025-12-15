import asyncio, re
from core.telnet_common import telnet_connect, send_command

# ================== PARSERS ==================
def parse_switch_info(output: str):
    match = re.search(r"System Description:\s+(.+)", output)
    if not match:
        return None

    desc = match.group(1).strip()
    if "MES" not in desc:
        return None

    model_match = re.search(r"(MES[0-9A-Za-z]+)", desc)
    model = model_match.group(1) if model_match else "Unknown"

    ports_match = re.search(r"(\d+)-port", desc)
    ports = ports_match.group(1) if ports_match else "Unknown"

    speed_match = re.search(r"(\d+[MG]/\d+[MG])", desc)
    speed = speed_match.group(1) if speed_match else "Unknown"

    return {
        "model": model,
        "ports": ports,
        "speed": speed,
        "description": desc
    }

def find_mes_presence(outputs: dict):
    for text in outputs.values():
        if "MES" in text:
            return True
    return False

def determine_interface_type(speed: str):
    if speed in ("100M/1G", "100M/1G "):
        return "FastEthernet"
    elif speed in ("1G/10G", "1G/10G "):
        return "GigabitEthernet"
    else:
        return "FastEthernet"

def parse_interface(output: str):
    status_match = re.search(r"is (\w+) \(connected\)", output)
    status = status_match.group(1) if status_match else "down"

    if status.lower() != "up":
        return {"status": "down"}

    duplex_speed_match = re.search(r"Full-duplex, (\d+Mbps), .*media type is (\S+)", output)
    link_speed = duplex_speed_match.group(1) if duplex_speed_match else "Unknown"
    media_type = duplex_speed_match.group(2) if duplex_speed_match else "Unknown"

    input_match = re.search(r"15 second input rate is (\d+) Kbit/s", output)
    output_match = re.search(r"15 second output rate is (\d+) Kbit/s", output)
    input_rate = input_match.group(1) if input_match else "0"
    output_rate = output_match.group(1) if output_match else "0"

    input_errors_match = re.search(r"(\d+) input errors", output)
    output_errors_match = re.search(r"(\d+) output errors", output)
    input_errors = input_errors_match.group(1) if input_errors_match else "0"
    output_errors = output_errors_match.group(1) if output_errors_match else "0"

    return {
        "status": "up",
        "link_speed": link_speed,
        "media_type": media_type,
        "input_rate": input_rate,
        "output_rate": output_rate,
        "input_errors": input_errors,
        "output_errors": output_errors
    }

def parse_mac_table(output: str):
    mac_entries = []
    lines = output.splitlines()
    for line in lines:
        line = line.strip()
        match = re.match(r"^(\d+)\s+([0-9a-f:]{17})\s+(\S+)\s+(\S+)", line, re.I)
        if match:
            vlan, mac, port, type_ = match.groups()
            mac_entries.append({
                "vlan": vlan,
                "mac": mac,
                "port": port,
                "type": type_
            })
    return mac_entries

async def get_port_logs(reader, writer, short_port, max_lines=15):
    cmd = "show logging"
    writer.write(cmd + "\n")
    await asyncio.sleep(0.5)

    output = ""
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=1.5)
            if not chunk:
                break
            output += chunk
            # постраничный вывод
            if "More:" in chunk or "---- More ----" in chunk:
                writer.write(" ")
                await asyncio.sleep(0.2)
        except asyncio.TimeoutError:
            break

    # Фильтруем в Python по порту
    lines = []
    for line in output.splitlines():
        line = line.strip()
        if re.search(rf"\b{re.escape(short_port)}\b", line, re.I):
            lines.append(line)
    return lines[-max_lines:]

# ================== RUN ==================
async def run(host: str, password: str, port: str):
    print("➡ Running ELTEX diagnostics...")

    # Input PORT: 2 -> 1/0/2
    if "/" not in port:
        port = f"1/0/{port}"

    reader, writer = await telnet_connect(host, password)

    try:
        # ===== BASE COMMANDS =====
        commands = {
            "version": "show version",
            "system": "show system",
            "switch": "show switch"
        }

        outputs = {}
        for key, cmd in commands.items():
            outputs[key] = await send_command(reader, writer, cmd)

        if not find_mes_presence(outputs):
            print("❌ Устройство не является MES.")
            return 

        sys_info = parse_switch_info(outputs.get("system", ""))
        if not sys_info:
            print("⚠ Не удалось извлечь данные о коммутаторе.")
            return

        # ===== SWITCH INFO =====
        print("\n===== SWITCH DATA =====")
        print(f"✔ Модель           : {sys_info['model']}")
        print(f"✔ Кол-во портов    : {sys_info['ports']}")
        print(f"✔ Скорость свитча  : {sys_info['speed']}")

        # ===== PORT NORMALIZATION =====
        int_type = determine_interface_type(sys_info['speed'])
        full_port = f"{port}"
        short_port = f"{int_type[:2].lower()}{port}"

        # ===== INTERFACE INFO =====
        interface_cmd = f"show interfaces {int_type} {full_port}"
        int_output = await send_command(reader, writer, interface_cmd)
        port_info = parse_interface(int_output)

        # ===== BASIC INFO =====
        print("\n===== INFO =====")
        print(f"IP   : {host}")
        print(f"PORT : {full_port}")

        port_is_up = port_info["status"] == "up"

        # ===== STATUS =====
        print("\n===== PORT STATUS =====")
        print(f"Status: {port_info['status'].upper()}")

        # ===== UP STATE =====
        if port_is_up:
            print("\n===== LINK =====")
            print(f"Link speed : {port_info['link_speed']} Mbps")
            print(f"Media type : {port_info['media_type']}")

            print("\n===== PORT TRAFFIC =====")
            print(f"Input rate  : {port_info['input_rate']} Kbit/s")
            print(f"Output rate : {port_info['output_rate']} Kbit/s")

            print("\n===== PORT ERRORS =====")
            print(f"Input errors  : {port_info['input_errors']}")
            print(f"Output errors : {port_info['output_errors']}")

            # ===== MAC TABLE =====
            mac_cmd = f"show mac address-table interface {int_type} {full_port}"
            mac_output = await send_command(reader, writer, mac_cmd)
            mac_entries = parse_mac_table(mac_output)

            print("\n===== PORT VLAN / MAC =====")
            if mac_entries:
                for entry in mac_entries:
                    print(f"VLAN: {entry['vlan']}")
                    print(f"MAC : {entry['mac']}")
                    print(f"Type: {entry['type']}")
                    print("-" * 25)
            else:
                print("⚠ MAC-адреса на порту не найдены.")

        # ===== DOWN STATE =====
        else:
            print("\n[ L1 ] Возможна физическая проблема")
            print("❌ Порт не активен (DOWN)")
            print("Рекомендации:")
            print("  - Проверьте кабель")
            print("  - Проверьте питание устройства")
            print("  - Проверьте удалённую сторону")

        # ===== LOGS (ALWAYS) =====
        port_logs = await get_port_logs(reader, writer, short_port, max_lines=15)

        print("\n===== DEVICE LOGS =====")
        if port_logs:
            for line in port_logs:
                print(line)
        else:
            print("⚠ Логи для порта не найдены.")

    finally:
        writer.close()
