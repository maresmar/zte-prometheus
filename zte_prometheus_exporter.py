#!/usr/bin/env python3
import json
import logging
import time
from typing import Optional
from wsgiref.simple_server import make_server

import requests
from prometheus_client import CollectorRegistry, Gauge, Info, make_wsgi_app

ROUTER_BASE_URL = "http://192.168.0.1"
LISTEN_ADDRESS = "0.0.0.0"
LISTEN_PORT = 9105
POLL_INTERVAL_SECONDS = 300  # 5 minutes is fine for a yearly data plan counter
REQUEST_TIMEOUT_SECONDS = 10

CMD_FIELDS = [
    "network_type",
    "rssi",
    "rscp",
    "lte_rsrp",
    "monthly_rx_bytes",
    "monthly_tx_bytes",
]

HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{ROUTER_BASE_URL}/index.html",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "zte-prometheus-exporter/1.0",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

session = requests.Session()
registry = CollectorRegistry()

metric_up = Gauge(
    "zte_modem_up",
    "Whether the modem query succeeded (1=success, 0=failure)",
    registry=registry,
)

metric_network = Info(
    "zte_modem_network",
    "Current radio network type",
    registry=registry,
)

metric_rssi = Gauge(
    "zte_modem_rssi_dbm",
    "RSSI in dBm",
    registry=registry,
)

metric_rscp = Gauge(
    "zte_modem_rscp_dbm",
    "RSCP in dBm (typically 3G only)",
    registry=registry,
)

metric_lte_rsrp = Gauge(
    "zte_modem_lte_rsrp_dbm",
    "LTE RSRP in dBm",
    registry=registry,
)

metric_monthly_rx_bytes = Gauge(
    "zte_modem_monthly_rx_bytes",
    "Monthly received bytes reported by the modem",
    registry=registry,
)

metric_monthly_tx_bytes = Gauge(
    "zte_modem_monthly_tx_bytes",
    "Monthly transmitted bytes reported by the modem",
    registry=registry,
)

metric_monthly_total_bytes = Gauge(
    "zte_modem_monthly_total_bytes",
    "Monthly total bytes reported by the modem",
    registry=registry,
)

metric_scrape_duration = Gauge(
    "zte_modem_scrape_duration_seconds",
    "Duration of the last modem scrape",
    registry=registry,
)

metric_scrape_timestamp = Gauge(
    "zte_modem_last_success_unixtime",
    "Unix timestamp of the last successful modem scrape",
    registry=registry,
)


def build_url() -> str:
    ts_ms = int(time.time() * 1000)
    cmd = ",".join(CMD_FIELDS)
    return (
        f"{ROUTER_BASE_URL}/goform/goform_get_cmd_process"
        f"?isTest=false"
        f"&cmd={cmd}"
        f"&multi_data=1"
        f"&_={ts_ms}"
    )


def parse_number(value: object) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def set_gauge_if_present(metric: Gauge, value: object) -> Optional[float]:
    parsed = parse_number(value)
    if parsed is not None:
        metric.set(parsed)
    return parsed


def scrape_modem() -> None:
    start = time.time()

    try:
        response = session.get(
            build_url(),
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

        logging.info("Modem response: %s", json.dumps(data, sort_keys=True))

        network_type = str(data.get("network_type", "")).strip() or "unknown"
        metric_network.info({"network_type": network_type})

        set_gauge_if_present(metric_rssi, data.get("rssi"))
        set_gauge_if_present(metric_rscp, data.get("rscp"))
        set_gauge_if_present(metric_lte_rsrp, data.get("lte_rsrp"))

        monthly_rx = set_gauge_if_present(
            metric_monthly_rx_bytes, data.get("monthly_rx_bytes"))
        monthly_tx = set_gauge_if_present(
            metric_monthly_tx_bytes, data.get("monthly_tx_bytes"))

        if monthly_rx is not None and monthly_tx is not None:
            metric_monthly_total_bytes.set(monthly_rx + monthly_tx)

        metric_up.set(1)
        metric_scrape_timestamp.set_to_current_time()

    except Exception as exc:
        metric_up.set(0)
        logging.warning("Scrape failed: %s", exc)

    finally:
        metric_scrape_duration.set(time.time() - start)


def main() -> None:
    # Prime metrics once before serving.
    scrape_modem()

    # Expose /metrics via WSGI. Compression is enabled by default
    # in prometheus_client's WSGI app when the client supports gzip.
    app = make_wsgi_app(registry=registry)

    httpd = make_server(LISTEN_ADDRESS, LISTEN_PORT, app)
    logging.info("Serving metrics on http://%s:%s/metrics",
                 LISTEN_ADDRESS, LISTEN_PORT)

    next_scrape = 0.0
    while True:
        now = time.time()
        if now >= next_scrape:
            scrape_modem()
            next_scrape = now + POLL_INTERVAL_SECONDS

        httpd.timeout = 1
        httpd.handle_request()


if __name__ == "__main__":
    main()
