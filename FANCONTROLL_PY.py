import time, json, socket, signal, sys, threading, requests, csv
from io import StringIO
from contextlib import closing

# InfluxDB 2.x "읽기(쿼리)" 엔드포인트 (풀 URL)
INFLUXDB_QUERY_URL = "http://localhost:8086/api/v2/query?org=HANBAT"
INFLUXDB_TOKEN     = "RCT4a8V-f35ri3UYcz5Z3-KfHhTGInyE8PJVLMpmzRT96E6KcpFgbzJ5H5S6p-9qhVUb_tS4BHAvLRBOaKW7-g=="   # 토큰 필수(로컬이어도 2.x는 기본 인증 필요)

# 버킷/스키마 (당신의 쓰기 코드에 맞춤)
INFLUX_BUCKET    = "TEMPER"
MEASUREMENT_CPU  = "cpu_temperature"
MEASUREMENT_GPU  = "gpu_temperature"
MEASUREMENT_MDL  = "model_result"
FIELD_NAME       = "value"  # 세 측정치 모두 field명이 'value'로 쓰이고 있음

# 제어 대상 Raspberry Pi (PWM 수신 TCP 서버)
PI_HOST = "192.168.43.6"
PI_PORT = 7000

# 루프/제어 파라미터
LOOP_MS        = 2000
TEMP_THRESHOLD = 40
FORCE_PWM      = 100

# 콘솔에서 동적 변경(자바와 동일 의미)
_temp_threshold = TEMP_THRESHOLD
_force_pwm      = FORCE_PWM

stop_event = threading.Event()
lock = threading.Lock()

def _flux_last_for(measurement: str):
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{FIELD_NAME}")
  |> last()
'''
    body = {
        "query": flux,
        "type": "flux",
        "dialect": {
            "annotations": ["datatype","group","default"],
            "header": True,
            "delimiter": ","
        }
    }
    headers = {
        "Authorization": f"Token {INFLUXDB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/csv"
    }
    resp = requests.post(INFLUXDB_QUERY_URL, headers=headers, json=body, timeout=10)
    if resp.status_code != 200:
        print(f"[DB] {measurement} HTTP {resp.status_code} body: {resp.text[:300]}")
        return {"ok": False, "value": None}
    f = StringIO(resp.text)
    reader = csv.DictReader(f)
    for row in reader:
        v = row.get("_value")
        if v is None or v == "":
            continue
        return {"ok": True, "value": v}
    return {"ok": True, "value": None}

def fetch_latest_from_influx():
    cpu_r = _flux_last_for(MEASUREMENT_CPU)
    gpu_r = _flux_last_for(MEASUREMENT_GPU)
    mdl_r = _flux_last_for(MEASUREMENT_MDL)

    # 파싱 (없으면 0)
    try: cpu = float(cpu_r["value"]) if cpu_r["value"] is not None else 0.0
    except: cpu = 0.0
    try: gpu = float(gpu_r["value"]) if gpu_r["value"] is not None else 0.0
    except: gpu = 0.0
    try: model = int(float(mdl_r["value"])) if mdl_r["value"] is not None else 0
    except: model = 0

    # 세 값이 전부 None/0으로만 나오는지 확인해 보고 싶다면 여기에 디버그 프린트 추가 가능
    # print("[DBG]", cpu_r, gpu_r, mdl_r)

    # 최소 하나라도 들어왔으면 dict 반환
    if cpu_r["value"] is None and gpu_r["value"] is None and mdl_r["value"] is None:
        print("[DB] 최근 데이터가 없습니다 (세 measurement 모두 last() 결과 없음)")
        return None
    return {"cpu_temp": cpu, "gpu_temp": gpu, "model_result": model}
    
def calculate_pwm(cpu_temp, gpu_temp, model_result):
    # 자바와 동일 공식
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
        print(f"[SEND] → {PI_HOST}:{PI_PORT} {msg.strip()}")
    except Exception as e:
        print(f"[SEND] 실패: {e} (host={PI_HOST}, port={PI_PORT})")


def console_input_loop():
    if not sys.stdin.isatty():
        return
    import re
    global _temp_threshold, _force_pwm
    while not stop_event.is_set():
        try:
            raw = input(f"[설정] 임계 온도 입력 (현재 {_temp_threshold}): ").strip()
            if re.match(r"^\d+$", raw):
                with lock: _temp_threshold = int(raw)
            else:
                print("[설정] 정수만 입력하세요.")
            raw = input(f"[설정] 강제 PWM 값 입력 (현재 {_force_pwm}): ").strip()
            if re.match(r"^\d+$", raw):
                v = int(raw)
                if 0 <= v <= 100:
                    with lock: _force_pwm = v
                else:
                    print("[설정] 0~100만 허용")
            else:
                print("[설정] 정수만 입력하세요.")
            print(f"[설정] 업데이트: tempThreshold={_temp_threshold}, forcePwm={_force_pwm}")
        except EOFError:
            break
        except Exception as e:
            print(f"[설정] 입력 오류: {e}")


def control_loop():
    period = max(50, LOOP_MS) / 1000.0
    print(f"[LOOP] 시작: 주기={period:.3f}s, target={PI_HOST}:{PI_PORT}, bucket={INFLUX_BUCKET}/(cpu|gpu|model)")

    while not stop_event.is_set():
        t0 = time.time()
        try:
            row = fetch_latest_from_influx()
            if not row:
                print("[DB] 최근 데이터 없음/쿼리 실패 → PWM=0 전송")
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





