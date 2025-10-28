import os
import sys
import json
import time
import socket
import signal
import threading
from contextlib import closing

from influxdb_client import InfluxDBClient
from influxdb_client.client.exceptions import InfluxDBError

# ===== 환경설정 =====
INFLUX_URL    = os.getenv("INFLUX_URL", "http://localhost:8086").strip()
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN", "RCT4a8V-f35ri3UYcz5Z3-KfHhTGInyE8PJVLMpmzRT96E6KcpFgbzJ5H5S6p-9qhVUb_tS4BHAvLRBOaKW7-g==").strip()
INFLUX_ORG    = os.getenv("INFLUX_ORG", "HANBAT").strip()
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "TEMPER").strip()

MEASUREMENT = os.getenv("MEASUREMENT", "system_status")
FIELD_CPU   = os.getenv("FIELD_CPU", "cpu_temp")
FIELD_GPU   = os.getenv("FIELD_GPU", "gpu_temp")
FIELD_MODEL = os.getenv("FIELD_MODEL", "model_result")

PI_HOST   = os.getenv("PI_HOST", "192.168.43.6")
PI_PORT   = int(os.getenv("PI_PORT", "7000"))
LOOP_MS   = int(os.getenv("LOOP_MS", "2000"))
THR_TEMP  = int(os.getenv("TEMP_THRESHOLD", "40"))
FORCE_PWM = int(os.getenv("FORCE_PWM", "100"))

# 콘솔에서 동적 변경(자바와 동일 의미)
_temp_threshold = THR_TEMP
_force_pwm      = FORCE_PWM

stop_event = threading.Event()
lock = threading.Lock()


def get_influx_client():
    if not (INFLUX_URL and INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET):
        raise RuntimeError("INFLUX_URL/INFLUX_TOKEN/INFLUX_ORG/INFLUX_BUCKET 환경변수를 모두 설정하세요.")
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=5000)


def fetch_latest_from_influx(client):
    """
    Flux로 measurement에서 CPU/GPU/MODEL의 최신값을 한 줄로 피벗해서 가져온다.
    최근 24시간 범위에서 last() 후 pivot.
    """
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT}" and
                      (r._field == "{FIELD_CPU}" or r._field == "{FIELD_GPU}" or r._field == "{FIELD_MODEL}"))
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time","{FIELD_CPU}","{FIELD_GPU}","{FIELD_MODEL}"])
'''
    query_api = client.query_api()
    try:
        tables = query_api.query(org=INFLUX_ORG, query=flux)
        if not tables:
            return None
        # pivot 이후는 하나의 레코드 테이블일 가능성이 높음
        for table in tables:
            for rec in table.records:
                # 각 필드가 없을 수도 있으니 get으로 안전하게
                row = {
                    "cpu_temp": float(rec.values.get(FIELD_CPU, 0.0) or 0.0),
                    "gpu_temp": float(rec.values.get(FIELD_GPU, 0.0) or 0.0),
                    "model_result": int(float(rec.values.get(FIELD_MODEL, 0) or 0)),
                }
                return row
        return None
    except InfluxDBError as e:
        print(f"[DB] Influx 쿼리 오류: {e}", flush=True)
        return None
    except Exception as e:
        print(f"[DB] 예외: {e}", flush=True)
        return None


def calculate_pwm(cpu_temp: float, gpu_temp: float, model_result: int) -> int:
    """
    자바 버전과 동일 공식:
      f_cpu = cpu/60, f_gpu = gpu/60
      pwm = (12 + 88 * max(f_cpu, f_gpu)) * f_model
    범위: 0~100
    """
    f_cpu = cpu_temp / 60.0
    f_gpu = gpu_temp / 60.0
    f_model = float(model_result)  # 0 또는 1
    pwm = (12.0 + 88.0 * max(f_cpu, f_gpu)) * f_model
    pwm = max(0, min(100, int(round(pwm))))
    return pwm


def send_to_pi(pwm_value: int):
    msg = json.dumps({"pwm": int(pwm_value)}) + "\n"
    try:
        with closing(socket.create_connection((PI_HOST, PI_PORT), timeout=5)) as s:
            s.sendall(msg.encode("utf-8"))
        print(f"[SEND] → {PI_HOST}:{PI_PORT} {msg.strip()}", flush=True)
    except Exception as e:
        print(f"[SEND] 실패: {e} (host={PI_HOST}, port={PI_PORT})", flush=True)


def console_input_loop():
    # 서비스(systemd) 환경에서는 보통 TTY 없음 → 자동 비활성
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
                    print("[설정] 0~100 사이 값만 허용합니다.")
            else:
                print("[설정] 정수만 입력하세요.")

            print(f"[설정] 업데이트: tempThreshold={_temp_threshold}, forcePwm={_force_pwm}", flush=True)
        except EOFError:
            break
        except Exception as e:
            print(f"[설정] 입력 오류: {e}", flush=True)


def control_loop():
    period = max(50, LOOP_MS) / 1000.0
    print(f"[LOOP] 시작: 주기={period:.3f}s, target={PI_HOST}:{PI_PORT}, bucket={INFLUX_BUCKET}/{MEASUREMENT}", flush=True)

    client = get_influx_client()
    while not stop_event.is_set():
        t0 = time.time()
        try:
            row = fetch_latest_from_influx(client)
            if not row:
                print("[DB] 최근 데이터 없음(또는 쿼리 실패) → PWM=0 전송", flush=True)
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
                    print(f"[CTRL] 임계 초과({thr}°C) → 강제 PWM={fpw} (cpu={cpu:.1f}, gpu={gpu:.1f}, model={model})", flush=True)
                else:
                    print(f"[CTRL] calc pwm={pwm} (cpu={cpu:.1f}, gpu={gpu:.1f}, model={model})", flush=True)

                send_to_pi(pwm)

        except Exception as e:
            print(f"[CTRL] 루프 예외: {e}", flush=True)

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
        print("[MAIN] 종료", flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    main()
