import time
import json
import socket
import signal
import sys
import threading
from contextlib import closing
import requests
import csv
from io import StringIO

# ===================== 고정 상수 설정(환경변수 쓰지 않음) =====================

# InfluxDB 2.x "읽기(쿼리)" 엔드포인트 (풀 URL 형태)
INFLUXDB_QUERY_URL = "http://localhost:8086/api/v2/query?org=HANBAT"
INFLUXDB_TOKEN     = "RCT4a8V-f35ri3UYcz5Z3-KfHhTGInyE8PJVLMpmzRT96E6KcpFgbzJ5H5S6p-9qhVUb_tS4BHAvLRBOaKW7-g=="

# Influx 쿼리 스키마(필요에 맞게 수정)
INFLUX_BUCKET = "TEMPER"
MEASUREMENT   = "system_status"
FIELD_CPU     = "cpu_temp"
FIELD_GPU     = "gpu_temp"
FIELD_MODEL   = "model_result"

# 제어 대상 Raspberry Pi
PI_HOST = "192.168.43.6"
PI_PORT = 7000

# 루프/제어 파라미터
LOOP_MS       = 2000   # 2초
TEMP_THRESHOLD = 40    # 임계 온도(℃)
FORCE_PWM      = 100   # 임계 초과 시 강제 PWM(0~100)

# 콘솔에서 동적 변경(자바 코드와 동일 의미)
_temp_threshold = TEMP_THRESHOLD
_force_pwm      = FORCE_PWM

stop_event = threading.Event()
lock = threading.Lock()


def fetch_latest_from_influx():
    """
    Flux로 CPU/GPU/MODEL의 최신값을 한 줄로 가져온다.
    응답은 CSV로 받으며, pivot 후 컬럼 이름이 FIELD_CPU/FIELD_GPU/FIELD_MODEL 이 되도록 함.
    """
    headers = {
        "Authorization": f"Token {INFLUXDB_TOKEN}",
        "Accept": "application/csv",
        "Content-Type": "application/vnd.flux"
    }

    flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT}" and
                      (r._field == "{FIELD_CPU}" or r._field == "{FIELD_GPU}" or r._field == "{FIELD_MODEL}"))
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time","{FIELD_CPU}","{FIELD_GPU}","{FIELD_MODEL}"])
"""

    try:
        # InfluxDB 2.x query API는 POST /api/v2/query 에 JSON 또는 text(Flux)로 보낼 수 있음
        # 여기서는 Flux를 본문에 그대로 보냄(Content-Type: application/vnd.flux)
        resp = requests.post(INFLUXDB_QUERY_URL, data=flux.encode("utf-8"), headers=headers, timeout=5)
        if resp.status_code != 200:
            print(f"[DB] HTTP {resp.status_code} {resp.text[:200]}")
            return None

        # CSV 파싱
        csv_text = resp.text
        # Influx CSV는 주석/주해 라인이 있을 수 있어 DictReader로 단순 파싱
        f = StringIO(csv_text)
        reader = csv.DictReader(f)
        # 첫 유효 레코드 반환
        for row in reader:
            # 공백/None 대비해서 안전 변환
            def to_float(x): 
                try: return float(x)
                except: return 0.0
            def to_int(x):
                try: return int(float(x))
                except: return 0

            return {
                "cpu_temp": to_float(row.get(FIELD_CPU)),
                "gpu_temp": to_float(row.get(FIELD_GPU)),
                "model_result": to_int(row.get(FIELD_MODEL)),
            }
        # 데이터 없음
        return None

    except requests.RequestException as e:
        print(f"[DB] 요청 예외: {e}")
        return None
    except Exception as e:
        print(f"[DB] 파싱 예외: {e}")
        return None


def calculate_pwm(cpu_temp: float, gpu_temp: float, model_result: int) -> int:
    """
    자바 버전과 동일한 공식:
      f_cpu = cpu/60, f_gpu = gpu/60
      pwm = (12 + 88 * max(f_cpu, f_gpu)) * f_model
    결과 0~100 정수로 클램프
    """
    f_cpu = cpu_temp / 60.0
    f_gpu = gpu_temp / 60.0
    f_model = float(model_result)  # 0 또는 1

    pwm = (12.0 + 88.0 * max(f_cpu, f_gpu)) * f_model
    pwm = max(0, min(100, int(round(pwm))))
    return pwm


def send_to_pi(pwm_value: int):
    """
    라즈베리파이로 TCP JSON line 전송: {"pwm": <int>}\n
    """
    msg = json.dumps({"pwm": int(pwm_value)}) + "\n"
    try:
        with closing(socket.create_connection((PI_HOST, PI_PORT), timeout=5)) as s:
            s.sendall(msg.encode("utf-8"))
        print(f"[SEND] → {PI_HOST}:{PI_PORT} {msg.strip()}")
    except Exception as e:
        print(f"[SEND] 실패: {e} (host={PI_HOST}, port={PI_PORT})")


def console_input_loop():
    """
    콘솔에서 임계온도/PWM을 변경 (systemd 등 TTY 없으면 자동 비활성)
    """
    if not sys.stdin.isatty():
        return
    import re
    global _temp_threshold, _force_pwm
    while not stop_event.is_set():
        try:
            raw = input(f"[설정] 임계 온도 입력 (현재 {_temp_threshold}): ").strip()
            if re.match(r"^\d+$", raw):
                with lock:
                    _temp_threshold = int(raw)
            else:
                print("[설정] 정수만 입력하세요.")

            raw = input(f"[설정] 강제 PWM 값 입력 (현재 {_force_pwm}): ").strip()
            if re.match(r"^\d+$", raw):
                v = int(raw)
                if 0 <= v <= 100:
                    with lock:
                        _force_pwm = v
                else:
                    print("[설정] 0~100 사이만 허용")
            else:
                print("[설정] 정수만 입력하세요.")

            print(f"[설정] 업데이트: tempThreshold={_temp_threshold}, forcePwm={_force_pwm}")
        except EOFError:
            break
        except Exception as e:
            print(f"[설정] 입력 오류: {e}")


def control_loop():
    period = max(50, LOOP_MS) / 1000.0
    print(f"[LOOP] 시작: 주기={period:.3f}s, target={PI_HOST}:{PI_PORT}, bucket={INFLUX_BUCKET}/{MEASUREMENT}")

    while not stop_event.is_set():
        t0 = time.time()
        try:
            row = fetch_latest_from_influx()
            if not row:
                print("[DB] 최근 데이터 없음(또는 쿼리 실패) → PWM=0 전송")
                send_to_pi(0)
            else:
                cpu = float(row["cpu_temp"])
                gpu = float(row["gpu_temp"])
                model = int(row["model_result"])

                pwm = calculate_pwm(cpu, gpu, model)

                with lock:
                    thr = _temp_threshold
                    fpw = _force_pwm

                if cpu >= thr or gpu >= thr:
                    pwm = fpw
                    print(f"[CTRL] 임계 초과({thr}°C) → 강제 PWM={fpw} (cpu={cpu:.1f}, gpu={gpu:.1f}, model={model})")
                else:
                    print(f"[CTRL] calc pwm={pwm} (cpu={cpu:.1f}, gpu={gpu:.1f}, model={model})")

                send_to_pi(pwm)

        except Exception as e:
            print(f"[CTRL] 루프 예외: {e}")

        # 고정 주기 유지
        elapsed = time.time() - t0
        time.sleep(max(0.0, period - elapsed))


def main():
    th = threading.Thread(target=console_input_loop, name="console-input", daemon=True)
    th.start()
    try:
        control_loop()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        print("[MAIN] 종료")


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    main()
