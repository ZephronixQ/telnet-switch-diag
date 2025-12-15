import asyncio
import telnetlib3
import re

ANSI = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')


def clean_line(line: str) -> str:
    """Удаляет ANSI-последовательности и лишние пробелы"""
    line = ANSI.sub('', line)
    line = re.sub(r"[^\x20-\x7E]+", " ", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


async def telnet_connect(host: str, password: str):
    """Создает Telnet-соединение и возвращает reader, writer"""
    reader, writer = await telnetlib3.open_connection(host=host, port=23)

    # login
    writer.write("admin\n")
    await asyncio.sleep(0.5)
    writer.write(password + "\n")
    await asyncio.sleep(0.5)

    return reader, writer


async def send_command(reader, writer, command, timeout=1.2):
    """Отправка команды и получение вывода с обработкой 'more'"""
    writer.write(command + "\n")
    await asyncio.sleep(0.3)
    output = ""

    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(2048), timeout=timeout)
            if not chunk:
                break

            output += chunk

            if "---- More ----" in chunk or "more" in chunk.lower():
                writer.write(" ")
                await asyncio.sleep(0.2)

        except asyncio.TimeoutError:
            break

    return output