import os
import time
import json
import socket
import csv
import io
from dataclasses import dataclass
import requests

# =========================
# 0) 환경설정 (필요 시 수정)
# =========================
ORG     = os.getenv("INFLUX_ORG", "HANBAT")
BUCKET  = os.getenv("INFLUX_BUCKET", "TEMPER")
TOKEN   = os.getenv("INFLUX_TOKEN", "RCT4a8V-f35ri3UYcz5Z3-KfHhTGInyE8PJVLMpmzRT96E6KcpFgbzJ5H5S6p-9qhVUb_tS4BHAvLRBOaKW7-g==")
BASE    = os.getenv("INFLUX_URL_BASE", "http://localhost:8086")
QUERY_URL = f"{BASE}/api/v2/query?org={ORG}"

PI_HOST = os.getenv("PI_HOST", "192.168.43.6")
PI_PORT = int(os.getenv("PI_PORT", "7000"))

# =========================
# 1) Influx 쿼리
# =========================
headers = {
    "Authorization": f"Token {TOKEN}",
    "Content-Type": "application/vnd.flux",
    "Accept": "application/csv"
}

flux = f'''
from(bucket: "{BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "cpu_temperature" or
                       r._measurement == "gpu_temperature" or
                       r._measurement == "model_result")
  |> filter(fn: (r) => r._field == "value")
  |> group(columns: ["_measurement"])
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 1)
'''

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

@dataclass
class FanController:
    min_duty: int = 20
    slew_per_sec: int = 25
    t_on: float = 38.0
    t_off: float = 35.0
    last_pwm: int = 0
    last_ts_ms: int = 0

    def _target_by_formula(self, cpu_temp: float, gpu_temp: float, model_result: int) -> int:
        f_cpu = clamp(cpu_temp / 60.0, 0.0, 1.0)
        f_gpu = clamp(gpu_temp / 60.0, 0.0, 1.0)
        f_model = 1.0 if model_result > 0 else 0.0
        pwm = (35.0 + 88.0 * max(f_cpu, f_gpu) * f_model)
        print(pwm)
        return int(round(clamp(pwm, 0.0, 100.0)))

    def step(self, cpu_temp: float, gpu_temp: float, model_result: int) -> int:
        target = self._target_by_formula(cpu_temp, gpu_temp, model_result)
        T = max(cpu_temp, gpu_temp)

        # 히스테리시스
        gate_on = (self.last_pwm == 0 and T >= self.t_on) or (self.last_pwm > 0 and T >= self.t_off)
        if not gate_on:
            target = 0

        # 최소 듀티
        if target > 0 and target < self.min_duty:
            target = self.min_duty

        # 슬루 제한
        now = int(time.time() * 1000)
        dt = 1.0 if self.last_ts_ms == 0 else max(0.001, (now - self.last_ts_ms) / 1000.0)
        max_delta = int(round(self.slew_per_sec * dt))
        delta = max(-max_delta, min(max_delta, target - self.last_pwm))
        self.last_pwm = int(clamp(self.last_pwm + delta, 0, 100))
        self.last_ts_ms = now
        return self.last_pwm

    def step_255(self, cpu_temp: float, gpu_temp: float, model_result: int) -> int:
        return int(round(self.step(cpu_temp, gpu_temp, model_result) * 255.0 / 100.0))

def calculate_pwm_direct(cpu_temp: float, gpu_temp: float, model_result: int) -> int:
    """
    주어진 식 그대로 적용:
      f_cpu = cpu_temp / 60
      f_gpu = gpu_temp / 60
      f_model = 0 or 1
      pwm = 40 + 88 * max(f_cpu, f_gpu) * f_model
    """
    f_cpu = max(0.0, min(cpu_temp / 60.0, 1.0))
    f_gpu = max(0.0, min(gpu_temp / 60.0, 1.0))
    f_model = 1.0 if model_result > 0 else 0.0

    pwm = (12.0 + 88.0 * max(f_cpu, f_gpu)) * f_model
    pwm = max(0.0, min(pwm, 100.0))  # 0~100% 제한
    pwm_255 = int(round(pwm * 255.0 / 100.0))  # 0~255 변환
    return pwm_255

def read_latest_values():
    r = requests.post(QUERY_URL, headers=headers, data=flux, timeout=3)
    r.raise_for_status()
    csv_text = r.text.strip()
    reader = csv.DictReader(io.StringIO(csv_text))
    latest_values = {}
    for row in reader:
        m = row.get("_measurement")
        if not m:
            continue
        try:
            v = float(row["_value"]) if row["_value"] is not None else None
        except:
            v = None
        latest_values[m] = v
    return latest_values

_seq = 0
def send_to_pi(pwm_255: int):
    """
    PWM 값을 정수로만 전송 (ex: '180\n')
    라즈베리파이는 이를 그대로 수신해서 duty로 사용.
    """
    payload = f"{int(pwm_255)}\n".encode()
    with socket.create_connection((PI_HOST, PI_PORT), timeout=2) as s:
        s.sendall(payload)

def main():
    ctl = FanController()
    while True:
        try:
            vals = read_latest_values()
            cpu = vals.get("cpu_temperature")
            gpu = vals.get("gpu_temperature")
            model = vals.get("model_result")

            if cpu is None or gpu is None or model is None:
                print("[controller] missing data → PWM=0")
                send_to_pi(0)
                time.sleep(1.0)
                continue

            pwm_255 = calculate_pwm_direct(cpu, gpu, int(model))
            print(f"CPU={cpu:.1f}°C GPU={gpu:.1f}°C MODEL={int(model)} → PWM={pwm_255}% ({pwm_255}/255)")
            send_to_pi(pwm_255)

        except requests.HTTPError as he:
            print("[controller] HTTP error:", he)
            send_to_pi(0)
        except (socket.timeout, ConnectionRefusedError, OSError) as ne:
            print("[controller] network error:", ne)
        except Exception as e:
            print("[controller] error:", e)
            send_to_pi(0)

        time.sleep(1.0)

if __name__ == "__main__":
    main()





