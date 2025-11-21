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

PI_HOST = os.getenv("PI_HOST", "192.168.43.6") #Raspberry Pi IP
PI_PORT = int(os.getenv("PI_PORT", "6000")) #Raspberry Pi PORT

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
    min_duty: int = 30
    slew_per_sec: int = 25
    t_on: float = 25.0
    t_off: float = 20.0
    last_pwm: int = 0
    last_ts_ms: int = 0
    
    # 상태 관리 변수 추가 
    mode: str = "auto"  # "auto", "manual"
    manual_target: int = 0 # 수동 모드일 때 목표값 [Manual]
    
    # 임계값 저장 [Range]
    cpu_thresh: int = 40
    gpu_thresh: int = 40

    def _target_by_formula(self, cpu_temp: float, gpu_temp: float, model_result: int) -> int:
        f_cpu = clamp(cpu_temp / 60.0, 0.0, 1.0)
        f_gpu = clamp(gpu_temp / 60.0, 0.0, 1.0)
        f_model = 1.0 if model_result > 0 else 0.0
        pwm = 30.0 + (88.0 * max(f_cpu, f_gpu) * f_model)
        return int(round(clamp(pwm, 0.0, 100.0)))

    # step 함수 단순화: 내부 상태(mode)를 보고 알아서 결정하도록 변경
    def step(self, cpu_temp: float, gpu_temp: float, model_result: int) -> int:
        
        if self.mode == "manual":
            # 프론트에서 준 manual_target 그대로 사용
            target = clamp(self.manual_target, 0, 100)

        elif self.mode == "range":
            target = self._calculate_pwm_range(cpu_temp, gpu_temp)

        else:  # "auto"
            target = self._target_by_formula(cpu_temp, gpu_temp, model_result)

        # 2. 슬루 레이트 및 히스테리시스 적용 (급격한 변화 방지)
        T = max(cpu_temp, gpu_temp)
        gate_on = (self.last_pwm == 0 and T >= self.t_on) or (self.last_pwm > 0 and T >= self.t_off)
        
        # 팬이 꺼져있는데 켜질 온도가 아니면 0 유지 (단, 수동모드면 무시하고 돔)
        if self.mode in ("auto", "range") and not gate_on:
            target = 0

        # PWM 변화량 제한 (Slew Rate)
        now = int(time.time() * 1000)
        dt = 1.0 if self.last_ts_ms == 0 else max(0.001, (now - self.last_ts_ms) / 1000.0)
        max_delta = int(round(self.slew_per_sec * dt))
        delta = max(-max_delta, min(max_delta, target - self.last_pwm))
        
        self.last_pwm = int(clamp(self.last_pwm + delta, 0, 100))
        self.last_ts_ms = now
        
        return self.last_pwm
     
    def _calculate_pwm_range(self, cpu_temp: float, gpu_temp: float, cpu_threshold: int, gpu_threshold: int) -> int:
        """
        range 모드에서는 CPU와 GPU의 경계 온도를 설정하고, 해당 온도 이하일 경우 최소 PWM으로 설정,
        그 이상일 경우 자동 모드로 전환하여 계산.
        """
        # CPU / GPU 온도 경계값에 따른 PWM 계산
        if cpu_temp <= cpu_threshold and gpu_temp <= gpu_threshold:
            pwm = self.min_duty  # 최소 PWM 값
        else:
            pwm = self._target_by_formula(cpu_temp, gpu_temp, 1) # auto로 전환
            
        # PWM 값 클램프 (0~255 범위)
        pwm = int(clamp(pwm, 0, 255))
        
        return pwm

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
def send_to_pi(pwm_value: int):    
    payload = json.dumps({"pwm": int(pwm_value)}).encode() # JSON 포맷으로 변경
    try:
        with socket.create_connection((PI_HOST, PI_PORT), timeout=2) as s:
            s.sendall(payload)
    except Exception as e:
        print(f"[Network] Pi 전송 실패: {e}")

if __name__ == "__main__":
    print("이 파일은 라이브러리입니다. process_control_command.py를 실행하세요.")


