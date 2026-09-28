import json
import datetime

TIMELINE_LOG = "/home/kali/Desktop/Sniffer/ota_timeline_sensor.jsonl"

def log_timeline(event, seq, tech, ts_override=None, **extra):
    """
    Regista um evento de timeline com timestamp UTC de parede (NTP-synced).

    ts_override: permite capturar o timestamp num ponto anterior do codigo
    e registá-lo retroativamente quando o seq já é conhecido.
    """
    now = ts_override if ts_override else datetime.datetime.now(datetime.timezone.utc)
    entry = {
        "event": event,
        "ts_iso": now.isoformat(timespec='milliseconds'),
        "ts_unix_ms": int(now.timestamp() * 1000),
        "seq": seq,
        "tech": tech,
    }
    entry.update(extra)
    line = json.dumps(entry)
    with open(TIMELINE_LOG, "a") as f:
        f.write(line + "\n")
    print(f"[OTA-TIMELINE] {line}")
