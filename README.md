# MoniCrowd Crowd Sensor

**Edge software for privacy-preserving Wi-Fi crowd sensing with multi-RAN connectivity (Wi-Fi and LoRaWAN)**

---

## Overview

This repository contains the software that runs on each **MoniCrowd sensor**.

The sensor passively captures Wi-Fi probe requests sent by nearby mobile devices, builds anonymous device fingerprints, and uses an LSTM model to estimate how many devices are present. Only the **estimated count** is sent to the MoniCrowd server ([`crowd-server`](https://github.com/sensinglab/crowd-server)), which stores, visualizes and raises alerts on the data.

The sensor is designed for temporary deployments where connectivity is unreliable. It uses Wi-Fi when available and switches automatically to LoRaWAN when Wi-Fi fails. Measurements that cannot be sent are kept locally and resent later.

The repository also includes the **3D model of the sensor case**.

---

## Hardware

| Component | Role |
|---|---|
| Raspberry Pi 5 (Kali Linux) | Main processing unit |
| `wlan0` (built-in Wi-Fi) | Uplink to the cloud and setup hotspot |
| `wlan1` (USB Wi-Fi adapter) | Probe-request capture in monitor mode |
| RAK3172 LoRaWAN module (`/dev/ttyAMA0`) | LoRaWAN uplink and downlink |
| USB GPS receiver | Sensor position |
| Reset button (GPIO 12) | Enters setup mode at boot |

---

## Main Components

### 1. Device Detection and Fingerprinting

`Sniffer/crowdingSniffer.py` captures probe requests on `wlan1` (channel 1).

For each probe request it:

- Keeps only devices from mobile manufacturers (OUI list) or with randomized MAC addresses.
- Ignores weak signals below the configured power threshold (`Power_Filtration`).
- Builds a fingerprint from selected Information Elements (IDs 1, 45, 50, 59, 70, 107, 127, 191 and vendor-specific 221), masking unstable bits.
- Hashes the result with **t1ha0** to get a short fingerprint.
- Marks whether the packet starts a new transmission burst.
- Stores the record in a SQLite database kept in a **RAM disk** (`MemoryDB/`).

Records older than 30 minutes are deleted every hour by `Sniffer/dataRetentionManager.py`.

A known calibration device (fixed MAC) is handled separately: its RSSI is published to the `calibration/rssi` MQTT topic.

### 2. Device Count Estimation (LSTM)

`sendCrowdingData.py` runs every 5 minutes. It reads the last 15 minutes of records and computes six features:

| Feature | Meaning |
|---|---|
| `Total_Packets` | Probe requests captured |
| `Total_Bursts` | Transmission bursts |
| `Unique_MACs` | Distinct MAC addresses |
| `Unique_Fingerprints` | Distinct fingerprints |
| `Packets_Per_Fingerprint` | Packets / fingerprints |
| `Bursts_Per_Fingerprint` | Bursts / fingerprints |

The last 3 windows are scaled (`wifi_lstm_scaler.pkl`, StandardScaler) and passed to the LSTM model (`wifi_lstm_regressor.keras`: LSTM 32 → Dense 16 → Dense 1). The output is the estimated number of devices.

The model corrects the errors that fingerprinting alone cannot avoid, such as one device producing several fingerprints or different devices sharing the same one.

### 3. Multi-RAN Connectivity and Handover

`sensorCommunicationCheck.py` runs every 5 minutes and decides which technology to use:

1. **Wi-Fi (priority 1):** tries all configured Wi-Fi networks (`sensor-wifi-*` profiles) in order and checks that the server is reachable.
2. **LoRaWAN (priority 2):** if no Wi-Fi works, evaluates the configured LoRaWAN networks (Helium first, TTN last) and joins the first one available.

When Wi-Fi comes back, the sensor returns to Wi-Fi.

The RAK3172 is shared by several scripts, so access to the serial port is protected by a lock (`uart_lock.py`). The LoRaWAN duty cycle is also respected: if the module is restricted, measurements are only stored.

### 4. Reliable Delivery

Every measurement gets a sequence number (0–255) and is saved in a local buffer (`DB/StoredMeasurements.db`) before being sent.

- **Wi-Fi:** missing measurements are resent in one MQTT session.
- **LoRaWAN:** the server sends the last sequence number it received (FPort 5), and the sensor resends the missing ones in compact batches (FPort 4).

### 5. Configuration

There are three ways to configure the sensor:

| Method | How it works |
|---|---|
| **Local web page** | Hold the reset button for 5 s during the first 40 s after boot. The sensor creates the `CrowdSensor-Setup` hotspot and serves a configuration page on `10.42.0.1:5000`. This mode also starts automatically if the sensor has no configuration yet. |
| **Remote (MQTT)** | `service_scripts/downlink_listener.py` receives commands: `set_config`, `activate`, `disable`, `reboot`, `shutdown`. |
| **Remote (LoRaWAN)** | Downlinks on FPort 20: activate (`0x01`), disable (`0x02`), sensitivity (`0x03`). The sensor replies on FPort 21. |

The configuration is saved in `sensor-config-site/data/sensor_config.toml` (not versioned) and applied to the local database by `sensorConfigurationRemotely.py`.

`sensorConfiguration.py` provides a terminal menu for manual configuration.

### 6. Location

`sendSensorLocation.py` sends the sensor position, read from the GPS when available or from the configuration otherwise. It can be sent only at boot or periodically.

### 7. Logging

| File | Content |
|---|---|
| `logs/comm_events.jsonl` | Connectivity checks, handovers, sends, replays (one JSON event per line) |
| `Sniffer/ota_timeline_sensor.jsonl` | Timeline of each measurement, used to measure end-to-end delays |
| `Sniffer/startup.log`, `Sniffer/sniffer_error.log` | Sniffer startup and errors |

---

## Repository Organization

| Path | Purpose |
|---|---|
| `Sniffer/` | Probe-request capture, fingerprinting, OUI lists, data retention, LSTM model and scaler |
| `Sniffer/t1ha0/` | t1ha0 hash in C, with the build script for the Python module |
| `sendCrowdingData.py` | Feature extraction, LSTM inference and upload of the device count |
| `sendSensorLocation.py` | Upload of the sensor position |
| `sensorCommunicationAvailable.py` | Detects available interfaces at boot |
| `sensorCommunicationCheck.py` | Periodic connectivity check and handover |
| `communication_manager.py` | Wi-Fi sending and replay of stored measurements |
| `sensorFunctions.py` | Shared functions (MQTT, LoRaWAN, Wi-Fi failover, GPS, buffer, cron) |
| `sensorStartup.py` | Prepares the RAM disk and starts the sniffer |
| `sensorConfiguration.py`, `sensorCheckConfig.py` | Manual configuration and configuration check |
| `sensorConfigurationRemotely.py` | Applies the TOML configuration to the local database |
| `sensor-config-site/` | Local configuration web page (Flask) |
| `service_scripts/` | Reset button and remote command listener |
| `swARM_at_custom/` | Modified driver for the RAK3172 and RAK4270 LoRaWAN modules |
| `uart_lock.py` | Lock for the LoRaWAN serial port |
| `event_logger.py`, `timeline_logger.py` | Structured logging |
| `DB/` | Local SQLite databases (configuration and stored measurements) |
| `Services/system_services.md` | systemd services used by the sensor |
| `cronjobs_default.txt`, `cronjobs_configured.txt` | Scheduled tasks before and after configuration |
| `sensor-case/` | 3D model of the sensor case |

---

## Data Flow

```text
 Mobile devices (Wi-Fi probe requests)
                 |
                 v
   wlan1 in monitor mode (crowdingSniffer.py)
                 |
                 v
   OUI / randomized MAC filter + RSSI filter
                 |
                 v
   IE fingerprint (t1ha0) + burst detection
                 |
                 v
     RAM disk database (30 min retention)
                 |
                 v
   6 features x 3 windows  ->  LSTM model
                 |
                 v
       Estimated device count + sequence number
                 |
                 v
          Local buffer (StoredMeasurements.db)
                 |
        +--------+--------+
        |                 |
        v                 v
   Wi-Fi (MQTT/TLS)   LoRaWAN (Helium / TTN)
        |                 |
        +--------+--------+
                 v
     MoniCrowd server (crowd-server)
```

---

## Scheduled Tasks

Once configured, the sensor runs these tasks through cron:

| When | Script | Task |
|---|---|---|
| Boot + 15 s | `sensorCommunicationAvailable.py` | Detect interfaces |
| Boot + 90 s | `sensorStartup.py` | Start the sniffer |
| Every 5 min | `sensorCommunicationCheck.py` | Check connectivity / handover |
| Every 5 min | `sendCrowdingData.py` | Estimate and send device count |
| Every 5 min | `sendSensorLocation.py` | Send location (if periodic mode) |
| Every hour | `Sniffer/dataRetentionManager.py 30` | Delete records older than 30 min |
| Weekly | `Sniffer/macOUIupdater.py` | Update the mobile manufacturer OUI list |

The crontab is generated automatically from the sensor configuration.

---

## LoRaWAN Ports

| FPort | Direction | Content |
|---|---|---|
| 1 | Uplink | Sequence number + device count (2–3 bytes) |
| 2 | Uplink | Latitude and longitude |
| 4 | Uplink | Resent measurements |
| 5 | Downlink | Last sequence number received by the server |
| 20 | Downlink | Remote command |
| 21 | Uplink | Command acknowledgement |

---

## Installation

The scripts use absolute paths and expect the repository contents in `/home/kali/Desktop`.

**1. Python dependencies**

```bash
sudo apt install gpsd python3-gps python3-lgpio
pip install scapy cffi pandas numpy scikit-learn joblib tensorflow paho-mqtt \
            pyserial netifaces pytz matplotlib termcolor flask toml tomli-w
pip install -e swARM_at_custom/
```

**2. Build the t1ha0 module**

```bash
cd /home/kali/Desktop/Sniffer/t1ha0
python3 build_t1ha0_module.py
```

**3. Stop NetworkManager from controlling `wlan1`**

Create `/etc/NetworkManager/conf.d/unmanage.conf`:

```ini
[main]
plugins=ifcfg-rh,keyfile

[keyfile]
unmanaged-devices=interface-name:wlan1
```

**4. Create the RAM disk**

```bash
mkdir -p /home/kali/Desktop/MemoryDB
```

Add to `/etc/fstab` file:

```text
tmpfs    /home/kali/Desktop/MemoryDB tmpfs   rw,nodev,nosuid,size=500M 0 0
```

**5. Hotspot password**

Create `/etc/crowdsensor/setup.env` (not versioned):

```text
HOTSPOT_PASSWORD=<at-least-8-characters>
```

```bash
sudo chown root:root /etc/crowdsensor/setup.env
sudo chmod 600 /etc/crowdsensor/setup.env
```

**6. systemd services**

Create the three services described in [`Services/system_services.md`](Services/system_services.md) and enable them:

```bash
sudo systemctl daemon-reload
sudo systemctl enable sensor-setup.service
sudo systemctl enable setup-button-window.service
sudo systemctl enable sensor-downlink-listener.service
```

**7. Configure the sensor**

Reboot. Since there is no configuration yet, the sensor opens the `CrowdSensor-Setup` hotspot. Connect to it and open `http://10.42.0.1:5000` to set the sensor name, location, Wi-Fi networks, LoRaWAN credentials and upload settings.

---

## Sensor Case

The [`sensor-case/`](sensor-case/) folder contains the case designed for the MoniCrowd sensor (version 4.3):

| File | Use |
|---|---|
| `*-All-Chassis-v04.3.stl` | Chassis for 3D printing (GitHub shows a 3D preview) |
| `*-Lid-ShareViz-v04.3.dxf` | Lid drawing (for cutting) |
| `*-All-ShareViz-v04.3.glb` | 3D view of the full assembly (opens in most 3D viewers) |
| `*-All-ShareViz-v04.3.obj` + `.mtl` | Same 3D view in OBJ format |

---

## Privacy

- Only the **estimated device count** leaves the sensor. Fingerprints and MAC addresses are never transmitted.
- Device records are kept in a RAM disk, so they are lost on reboot, and are deleted after 30 minutes.
- Fingerprints are hashes, used only to count devices, not to identify people.
- Credentials (MQTT, LoRaWAN keys, hotspot password) stay on the device and are excluded from Git.

**Device count is not the same as people count.** Converting devices into people requires calibration.

---

## Relationship to the MoniCrowd Project

This repository is the **edge sensor** part of the MoniCrowd architecture. Related repositories:

- **MoniCrowd:** https://github.com/sensinglab/monicrowd
- **Crowd server:** https://github.com/sensinglab/crowd-server
- **Project website:** https://monicrowd.sensinglab.eu

---

## Contact

**SensingLab**

Website: https://monicrowd.sensinglab.eu

Email: info@sensinglab.eu
