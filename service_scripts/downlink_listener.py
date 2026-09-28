#!/usr/bin/env python3
import json
import os
import sys
import time
import threading
import subprocess
import ssl
from uuid import getnode

try:
    import tomllib
except ImportError:
    import toml as tomllib

try:
    import tomli_w
except ImportError:
    tomli_w = None

import paho.mqtt.client as mqtt

sys.path.append("/home/kali/Desktop/")
import sensorConfigurationRemotely


DEFAULT_MQTT_HOST = "t.monicrowd.sensinglab.eu"
DEFAULT_MQTT_PORT = 8883

SENSOR_UUID = str(getnode())

CMD_TOPIC = f"monicrowd/sensors/cmd/{SENSOR_UUID}"
SNAP_TOPIC = f"monicrowd/sensors/config_snapshot/{SENSOR_UUID}"
ACK_TOPIC = f"monicrowd/sensors/ack/{SENSOR_UUID}"

TOML_PATH = "/home/kali/Desktop/sensor-config-site/data/sensor_config.toml"
CONFIG_VERSION_FILE = "/home/kali/Desktop/.config_version"
LOCK_PATH = "/tmp/sensor_config.lock"


def load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)




def get_mqtt_settings() -> tuple[str, int, str, str]:
    """Load MQTT connection settings from the local, non-versioned TOML config."""
    cfg = load_toml(TOML_PATH)
    sensor_cfg = cfg.get("sensor", {})

    username = str(sensor_cfg.get("MQTT Username", "")).strip()
    password = str(sensor_cfg.get("MQTT Password", "")).strip()

    mqtt_host = DEFAULT_MQTT_HOST
    mqtt_port = DEFAULT_MQTT_PORT

    for entry in cfg.get("Connectivity", []):
        if entry.get("type") == "wifi":
            mqtt_host = str(entry.get("mqtt_address") or mqtt_host).strip()
            try:
                mqtt_port = int(entry.get("mqtt_port") or mqtt_port)
            except (TypeError, ValueError):
                mqtt_port = DEFAULT_MQTT_PORT
            break

    if not username or not password:
        raise RuntimeError("MQTT credentials are not configured in sensor_config.toml")

    return mqtt_host, mqtt_port, username, password


def atomic_write_toml(path: str, data: dict):
    tmp = path + ".tmp"

    if tomli_w is not None:
        content = tomli_w.dumps(data)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    else:
        import toml
        with open(tmp, "w", encoding="utf-8") as f:
            toml.dump(data, f)
            f.flush()
            os.fsync(f.fileno())

    os.replace(tmp, path)


def get_local_version() -> int:
    try:
        with open(CONFIG_VERSION_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def set_local_version(version: int):
    tmp = CONFIG_VERSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(int(version)))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_VERSION_FILE)


def with_lock(fn):
    def wrapper(*args, **kwargs):
        while True:
            try:
                fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                time.sleep(0.2)

        try:
            return fn(*args, **kwargs)
        finally:
            try:
                os.remove(LOCK_PATH)
            except FileNotFoundError:
                pass

    return wrapper


def apply_config_from_toml(path: str):
    """
    Chama a pipeline existente de configuração.
    Ajusta aqui se o nome real da função no sensorConfigurationRemotely.py for diferente.
    """
    if hasattr(sensorConfigurationRemotely, "apply_config_from_toml"):
        return sensorConfigurationRemotely.apply_config_from_toml(path)

    if hasattr(sensorConfigurationRemotely, "main"):
        return sensorConfigurationRemotely.main()

    subprocess.run(
        ["python3", "/home/kali/Desktop/sensorConfigurationRemotely.py"],
        check=False
    )


def publish_ack(mqttc, job_id=None, result="ok", error=None, config_version=None):
    payload = {
        "uuid": SENSOR_UUID,
        "result": result,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if job_id is not None:
        payload["job_id"] = job_id

    if config_version is not None:
        payload["config_version"] = config_version

    if error:
        payload["error"] = str(error)

    mqttc.publish(ACK_TOPIC, json.dumps(payload), qos=0, retain=False)


@with_lock
def apply_config_patch(patch: dict, config_version: int | None = None):
    cfg = load_toml(TOML_PATH)

    if "sensor" in patch and isinstance(patch["sensor"], dict):
        cfg.setdefault("sensor", {})
        for k, v in patch["sensor"].items():
            cfg["sensor"][k] = v

    if "Connectivity" in patch:
        if isinstance(patch["Connectivity"], list):
            cfg["Connectivity"] = patch["Connectivity"]

    atomic_write_toml(TOML_PATH, cfg)
    apply_config_from_toml(TOML_PATH)

    if config_version is not None:
        set_local_version(config_version)


@with_lock
def apply_full_config(full_config: dict, config_version: int):
    if not isinstance(full_config, dict):
        raise ValueError("snapshot config must be an object")

    if "sensor" not in full_config:
        raise ValueError("snapshot missing sensor section")

    if "Connectivity" not in full_config:
        raise ValueError("snapshot missing Connectivity section")

    atomic_write_toml(TOML_PATH, full_config)
    apply_config_from_toml(TOML_PATH)
    set_local_version(config_version)


def handle_snapshot(mqttc, data: dict):
    try:
        cloud_version = int(data.get("config_version", 0))
        local_version = get_local_version()

        print(f"[SYNC] local={local_version} cloud={cloud_version}")

        if cloud_version <= local_version:
            print("[SYNC] already up-to-date")
            publish_ack(
                mqttc,
                result="synced",
                config_version=local_version
            )
            return

        full_config = data.get("config")

        print(f"[SYNC] applying cloud snapshot v{cloud_version}")
        apply_full_config(full_config, cloud_version)

        publish_ack(
            mqttc,
            result="synced",
            config_version=cloud_version
        )

        print(f"[SYNC] updated to v{cloud_version}")

    except Exception as e:
        print(f"[ERROR][SYNC] {e}")
        publish_ack(
            mqttc,
            result="error",
            error=e
        )


def do_reboot():
    print("[REBOOT] A executar reboot agora...")
    subprocess.Popen(["sudo", "reboot"])


def do_shutdown():
    subprocess.Popen(["sudo", "shutdown", "-h", "now"])


def handle_command(mqttc, cmd: dict):
    job_id = cmd.get("job_id")
    ctype = (cmd.get("type") or "").strip().lower()
    payload = cmd.get("payload", {})
    config_version = cmd.get("config_version")

    if not job_id:
        return

    try:
        if ctype == "set_config":
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")

            apply_config_patch(payload, config_version=config_version)
            publish_ack(mqttc, job_id, "ok", config_version=config_version)

        elif ctype == "disable":
            apply_config_patch(
                {"sensor": {"Status": "Disabled"}},
                config_version=config_version
            )
            publish_ack(mqttc, job_id, "ok", config_version=config_version)

        elif ctype == "activate":
            apply_config_patch(
                {"sensor": {"Status": "Active"}},
                config_version=config_version
            )
            publish_ack(mqttc, job_id, "ok", config_version=config_version)

        elif ctype == "reboot":
            publish_ack(mqttc, job_id, "ok")
            do_reboot()

        elif ctype == "shutdown":
            publish_ack(mqttc, job_id, "ok")
            do_shutdown()

        else:
            publish_ack(
                mqttc,
                job_id,
                "error",
                error=f"unknown command_type: {ctype}"
            )

    except Exception as e:
        publish_ack(mqttc, job_id, "error", error=e)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[OK][MQTT] connected")

        client.subscribe(CMD_TOPIC, qos=0)
        print(f"[OK][MQTT] subscribed {CMD_TOPIC}")

        client.subscribe(SNAP_TOPIC, qos=1)
        print(f"[OK][MQTT] subscribed {SNAP_TOPIC}")

    else:
        print(f"[ERROR][MQTT] connect failed rc={rc}")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8", errors="replace"))
    except Exception:
        print(f"[WARN] invalid json topic={msg.topic}")
        return

    if msg.topic == SNAP_TOPIC:
        t = threading.Thread(
            target=handle_snapshot,
            args=(client, data),
            daemon=True
        )
        t.start()
        return

    if msg.topic == CMD_TOPIC:
        t = threading.Thread(
            target=handle_command,
            args=(client, data),
            daemon=True
        )
        t.start()
        return

    print(f"[INFO] ignored topic={msg.topic}")


def main():
    print(f"[OK] Downlink listener starting for uuid={SENSOR_UUID}")
    print(f"[OK] Local config version={get_local_version()}")

    mqtt_host, mqtt_port, mqtt_user, mqtt_password = get_mqtt_settings()

    c = mqtt.Client()
    c.username_pw_set(mqtt_user, mqtt_password)

    if mqtt_port == 8883:
        c.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)

    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(mqtt_host, mqtt_port, 60)
    c.loop_forever()


if __name__ == "__main__":
    main()