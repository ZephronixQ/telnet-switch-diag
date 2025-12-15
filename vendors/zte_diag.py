import asyncio
import telnetlib3
import re
from core.telnet_common import telnet_connect, send_command

# ================== PARSERS ==================
def extract(regex, text, default='N/A'):
    m = re.search(regex, text, re.I)
    return m.group(1) if m else default

def parse_zte_switch_info(output: str):
    model = extract(r'ZXR10\s+(\S+)', output, 'Unknown')
    m = re.search(r'Module 0:.*?fasteth:\s*(\d+);.*?gbit:\s*(\d+);', output, re.S)
    fasteth = int(m.group(1)) if m else 0
    gbit = int(m.group(2)) if m else 0
    total_ports = fasteth + gbit
    speed = f"FastEthernet x{fasteth}, Gigabit x{gbit}" if total_ports else "Unknown"

    return {
        "vendor": "ZTE",
        "model": model,
        "ports": total_ports,
        "speed": speed
    }

def is_zte(outputs: dict):
    version_output = outputs.get("version", "")
    return "ZXR10" in version_output

def parse_zte_mac(raw: str):
    table = []
    for line in raw.splitlines():
        cols = line.split()
        if not cols or 'MAC' in line.upper() or 'VLAN' in line.upper():
            continue
        if len(cols) >= 4:
            table.append({
                'mac': cols[0],
                'vlan': cols[1],
                'port': cols[2],
                'time': cols[11] if len(cols) > 11 else ''
            })
    return table

# ================== RUN ==================
async def run(host: str, password: str, port: str):
    print("➡ ZTE detected. Running ZTE diagnostics...")
    reader, writer = await telnet_connect(host, password)

    # ===== BASE COMMANDS =====
    commands = {
        "version": "show version",
        "system": "show system",
        "switch": "show switch"
    }

    outputs = {}
    for key, cmd in commands.items():
        outputs[key] = await send_command(reader, writer, cmd)

    if not is_zte(outputs):
        print("❌ Данное оборудование не является ZTE.")
        writer.close()
        return

    info = parse_zte_switch_info(outputs["version"])
    print("\n===== DEVICE INFO =====")
    print("Vendor:", info["vendor"])
    print("Model:", info["model"])
    print("Ports:", info["ports"])
    print("Speed:", info["speed"])

    # ===== ZTE SPECIFIC COMMANDS =====
    cmds = {
        'port': f'show port {port}',
        'mac_dynamic': f'show mac dynamic port {port}',
        'statistics': f'show port {port} statistics',
        'utilization': f'show port {port} utilization',
        'mac_protect': 'show mac protect',
        'dhcp': 'show dhcp relay binding',
        'logs': 'show terminal log include Port'
    }

    data = {}
    for key, cmd in cmds.items():
        data[key] = await send_command(reader, writer, cmd)

    # --- show port ---
    state = extract(r'\b(UP|DOWN)\b', data['port'])
    speed = extract(r'(\d+(?:\.\d+)?\s*[MG]bps?)', data['port'])
    port_down = state.upper() == 'DOWN'

    # --- DHCP ---
    dhcp_mac = dhcp_ip = dhcp_vlan = None
    for line in data['dhcp'].splitlines():
        cols = line.split()
        if len(cols) >= 6 and cols[4] == port:
            dhcp_mac = cols[0]
            dhcp_ip = cols[1]
            dhcp_vlan = cols[3]
            break

    # --- MAC таблица ---
    mac_table = parse_zte_mac(data['mac_dynamic'])
    real_port = port

    if dhcp_mac:
        mac_entry = next((m for m in mac_table if m['mac'].lower() == dhcp_mac.lower()), None)
        if mac_entry:
            real_port = re.findall(r'\d+', mac_entry['port'])[-1]

    # --- statistics ---
    in_err = extract(r'InMACRcvErr\s*:\s*(\d+)', data['statistics'], '0')
    crc = extract(r'CrcError\s*:\s*(\d+)', data['statistics'], '0')

    # --- utilization ---
    util = re.search(r'input\s*[:]*\s*([\d.,]+)%\s*,\s*output\s*[:]*\s*([\d.,]+)%', data['utilization'], re.I)
    input_val = output_val = '0.00%'
    if util:
        input_val = f"{float(util.group(1).replace(',', '.')):.2f}%"
        output_val = f"{float(util.group(2).replace(',', '.')):.2f}%"

    # ================== OUTPUT ==================
    print(f'\n------------ [PORT {port}] ------------')
    state = state.upper()
    port_down = state == 'DOWN'

    if not port_down:
        print("\n===== LINK =====")
        print('STATE:', state)
        print('SPEED:', speed)

        if dhcp_mac:
            print('\n===== DHCP =====')
            print('MAC:', dhcp_mac)
            print('IP:', dhcp_ip)
            print('VLAN:', dhcp_vlan)
            print('PORT:', port)
        else:
            print('\nDHCP данных нет')

        print('\n===== MAC TABLE =====')
        if mac_table:
            last = mac_table[-1]
            print('MAC:', last['mac'])
            print('TIME:', last['time'])
        else:
            print('Нет MAC записей')

        print("\n===== PORT TRAFFIC =====")
        print(f'Input: {input_val}\nOutput: {output_val}')

        print('\n===== PORT ERRORS =====')
        print('InMACRcvErr:', in_err)
        print('CrcError:', crc)

        print('\n===== MAC PROTECT =====')
        for line in data['mac_protect'].splitlines():
            cols = line.split()
            if cols and real_port in cols[0]:
                status = cols[2] if len(cols) > 2 else 'N/A'
                print(f'STATUS: {status}')
                break
    else:
        print("\n===== LINK =====")
        print('STATE:', state)
        print("\n[ L1 ] Возможна физическая проблема")
        print("❌ Порт не активен (DOWN)")
        print("Рекомендации:")
        print("  - Проверьте кабель")
        print("  - Проверьте питание устройства")
        print("  - Проверьте удалённую сторону")

    # ===== DEVICE LOGS =====
    print('\n===== DEVICE LOGS =====')
    pattern = re.compile(rf'Port\s*:\s*{re.escape(real_port)}\b')
    MAX_LOG_LINES = 15
    logs = [l for l in data['logs'].splitlines() if pattern.search(l)][:MAX_LOG_LINES]

    if logs:
        for log in logs:
            print("~", log)
    else:
        print("⚠ Логи для порта не найдены.")

    writer.close()
