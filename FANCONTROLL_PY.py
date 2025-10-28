import os
import sys
import json
import time
import socket
import signal
import threading
from contextlib import closing

# --- 설정 로딩 ---
DB_DSN         = os.getenv("DB_DSN", "").strip()
DB_TABLE       = os.getenv("DB_TABLE", "system_status")
PI_HOST        = os.getenv("PI_HOST", "192.168.43.6")
PI_PORT        = int(os.getenv("PI_PORT", "7000"))
LOOP_MS        = int(os.getenv("LOOP_MS", "2000"))
TEMP_THRESHOLD = int(os.getenv("TEMP_THRESHOLD", "40"))
FORCE_PWM      = int(os.getenv("FORCE_PWM", "100"))

# 콘솔 입력 스레드에서 갱신될 가변 설정(자바 코드와 동일 의미)
_temp_threshold = TEMP_THRESHOLD
_force_pwm      = FORCE_PWM

stop_event = threading.Event()
lock = threading.Lock()

# --- DB 세팅 (선택. DSN 미설정이면 더미 데이터로 동작 가능) ---
USE_DB = bool(DB_DSN)
engine = None
if USE_DB:
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(DB_DSN, pool_pre_ping=True, future=True)
    except Exception as e:
        print(f"[DB] SQLAlchemy 초기화 실패: {e}", flush=True)
        print("[DB] DSN을 점검하세요. (DB_DSN 미설정이면 DB 없이도 동작 가능)", flush=True)
        USE_DB = False


def get_latest_data():
    """
    최신 1행을 반환: {"cpu_temp": float, "gpu_temp": float, "model_result": int}
    DB_DSN이 없거나 쿼리 실패 시, 예시 더미 값을 반환(운영 시엔 DSN 설정 권장).
    """
    if not USE_DB or engine is None:
        # 더미(테스트) 데이터
        return {"cpu_temp": 45.0, "gpu_temp": 42.0, "model_result": 1}

    sql = text(f"""
        SELECT cpu_temp, gpu_temp, model_result
        FROM {DB_TABLE}
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    try:
        with engine.connect() as conn:
            row = conn.execute(sql).mappings().first()
            if not row:
                # 데이터 없으면 안전한 기본값
                return {"cpu_temp": 0.0, "gpu_temp": 0.0, "model_result": 0}
            return {
                "cpu_temp": float(row["cpu_temp"]),
                "gpu_temp": float(row["gpu_temp"]),
                "model_result": int(row["model_result"]),
            }
    except Exception as e:
        print(f"[DB] 조회 실패: {e}", flush=True)
        # 실패 시에도 루프가 계속 돌도록 더미 반환
        return {"cpu_temp": 0.0, "gpu_temp": 0.0, "model_result": 0}


def calculate_pwm(cpu_temp: float, gpu_temp: float, model_result: int) -> int:
    """
    자바 버전과 동일한 계산식:
      f_cpu = cpuTemp / 60.0
      f_gpu = gpuTemp / 60.0
      pwm   = (12 + 88 * max(f_cpu, f_gpu)) * f_model
    범위: 0~100 (정수 반올림)
    """
    f_cpu = cpu_temp / 60.0
    f_gpu = gpu_temp / 60.0
    f_model = float(model_result)  # 0 또는 1

    pwm = (12.0 + 88.0 * max(f_cpu, f_gpu)) * f_model
    pwm = max(0, min(100, int(round(pwm))))
    return pwm


def send_to_pi(pwm_value: int):
    """
    라즈베리파이로 JSON 라인 전송: {"pwm": <int>}\n
    """
    msg = json.dumps({"pwm": int(pwm_value)}) + "\n"
    try:
        with closing(socket.create_connection((PI_HOST, PI_PORT), timeout=5)) as s:
            s.sendall(msg.encode("utf-8"))
        print(f"[SEND] → {PI_HOST}:{PI_PORT} {msg.strip()}", flush=True)
    except Exception as e:
        print(f"[SEND] 실패: {e} (host={PI_HOST}, port={PI_PORT})", flush=True)


def console_input_loop():
    """
    콘솔에서 임계온도/PWM 값을 갱신 (자바의 consoleInputLoop와 동일).
    systemd 등 TTY 없는 환경에서는 자동 비활성.
    """
    if not sys.stdin.isatty():
        # 서비스로 돌 때는 콘솔 입력이 불가하므로 비활성
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
                val = int(raw)
                if 0 <= val <= 100:
                    with lock:
                        _force_pwm = val
                else:
                    print("[설정] 0~100 사이 값만 허용합니다.")
            else:
                print("[설정] 정수만 입력하세요.")

            print(f"[설정] 업데이트: tempThreshold={_temp_threshold}, forcePwm={_force_pwm}", flush=True)
        except EOFError:
            # 입력 스트림 닫힘
            break
        except Exception as e:
            print(f"[설정] 입력 오류: {e}", flush=True)


def control_loop():
    """
    고정 주기 실행 루프 (@Scheduled(fixedRate = 2000) 대체)
    """
    global _temp_threshold, _force_pwm
    period = max(50, LOOP_MS) / 1000.0  # 최소 50ms 보호
    print(f"[LOOP] 시작: 주기={period:.3f}s, DB_TABLE={DB_TABLE}, target={PI_HOST}:{PI_PORT}", flush=True)

    while not stop_event.is_set():
        t0 = time.time()
        try:
            row = get_latest_data()
            cpu_temp = float(row.get("cpu_temp", 0.0))
            gpu_temp = float(row.get("gpu_temp", 0.0))
            model_result = int(row.get("model_result", 0))

            pwm = calculate_pwm(cpu_temp, gpu_temp, model_result)

            with lock:
                thr = _temp_threshold
                fpw = _force_pwm

            # 임계 초과 시 강제 PWM
            if cpu_temp >= thr or gpu_temp >= thr:
                pwm = fpw
                print(f"[CTRL] 임계 초과({thr}°C) → 강제 PWM={fpw} (cpu={cpu_temp:.1f}, gpu={gpu_temp:.1f})", flush=True)
            else:
                print(f"[CTRL] calc pwm={pwm} (cpu={cpu_temp:.1f}, gpu={gpu_temp:.1f}, model={model_result})", flush=True)

            send_to_pi(pwm)

        except Exception as e:
            print(f"[CTRL] 루프 오류: {e}", flush=True)

        # fixed-rate 비슷하게: 주기 보정
        elapsed = time.time() - t0
        sleep_s = max(0.0, period - elapsed)
        time.sleep(sleep_s)


def main():
    # 콘솔 입력 스레드 (TTY 있을 때만)
    th_in = threading.Thread(target=console_input_loop, name="console-input", daemon=True)
    th_in.start()

    # 메인 제어 루프
    try:
        control_loop()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        print("[MAIN] 종료", flush=True)


if __name__ == "__main__":
    # 깔끔한 종료를 위해 시그널 핸들러 등록
    signal.signal(signal.SIGINT,  lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    main()