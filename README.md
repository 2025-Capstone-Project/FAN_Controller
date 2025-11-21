# 2025 Capstone Project - Fan Controller

**[2025-Capstone-Project/FAN_Controller](https://github.com/2025-Capstone-Project/FAN_Controller)**

이 코드는 InfluxDB에 수집된 시스템 온도 데이터(CPU, GPU, AI 모델 결과)를 실시간으로 분석하여, 원격지에 있는 라즈베리파이(Raspberry Pi)의 쿨링 팬 속도(PWM)를 지능적으로 제어하는 시스템입니다.

자동 제어(Auto Mode)와 웹 인터페이스를 통한 수동 제어(Manual Mode)를 모두 지원하며, 안정적인 온도 유지를 위해 히스테리시스(Hysteresis) 및 슬루 레이트(Slew Rate) 제한 알고리즘이 적용되어 있습니다.

## 시스템 아키텍처 (Architecture)

이 저장소는 크게 **제어 서버(Server)** 와 **엣지 디바이스(Edge Device)** 로 구성됩니다.

*   **Server Side**: InfluxDB 데이터를 분석하고 최적의 PWM 값을 계산합니다. 웹소켓 서버를 통해 사용자 입력을 받습니다.
*   **Edge Side (Raspberry Pi)**: 서버로부터 TCP 소켓 명령을 수신하여 실제 하드웨어 팬을 구동합니다.


### 파일 구성
*   `process_control_command.py`: **[메인 서버 실행 파일]** 웹소켓 서버 및 자동 제어 루프(Asyncio)를 담당합니다.
*   `FANCONTROLL_PY.py`: **[라이브러리 모듈]** 제어 알고리즘(Core Logic) 및 InfluxDB 통신 기능을 제공합니다.
*   `pi.py`: **[라즈베리파이 실행 파일]** TCP 소켓 명령 수신 및 GPIO PWM 제어를 담당합니다.


## 주요 기능 (Features)


### 1. 듀얼 모드 제어 (Dual Mode Control)
*   **Auto Mode:** CPU/GPU 온도 및 AI 모델 부하에 따라 PWM을 자동으로 조절합니다.
*   **Manual Mode:** 웹 인터페이스를 통해 사용자가 직접 팬 속도(0~100%)를 고정할 수 있습니다.


### 2. 안정적인 제어 알고리즘
*   **Hysteresis:** 온도가 경계값에서 등락할 때 팬이 빈번하게 켜졌다 꺼지는 현상을 방지합니다.
*   **Slew Rate Limiting:** 팬 속도의 급격한 변화를 막아 하드웨어 부하를 줄이고 소음을 억제합니다.


### 3. 실시간 모니터링 및 피드백
*   서버는 InfluxDB에서 최근 7일간의 데이터를 조회하여 제어에 반영합니다.
*   라즈베리파이는 실제 적용된 PWM 듀티 사이클을 InfluxDB로 다시 전송하여, 명령과 실제 동작의 일치 여부를 확인할 수 있습니다.
